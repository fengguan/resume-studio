"""Export real Word insert/delete revisions, independent of model generation."""

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.run import Run

from .parsing import document_paragraphs, load_document
from .rendering import RenderError, paragraph_map
from .schemas import Risk

FILENAME = "resume_comparison.docx"


def _tokens(text):
    # English words stay whole; CJK characters, punctuation and whitespace stay lossless.
    return re.findall(r"[\u3400-\u9fff]|[^\W_]+(?:['’][^\W_]+)*|_+|\s+|[^\w\s]", text)


def _revision_text(element, accept):
    """Read either revision view, including text inside hyperlinks and table cells."""
    if element.tag == qn("w:del") and accept:
        return ""
    if element.tag == qn("w:ins") and not accept:
        return ""
    if element.tag in (qn("w:t"), qn("w:delText")):
        return element.text or ""
    if element.tag == qn("w:tab"):
        return "\t"
    if element.tag == qn("w:cr") or (
        element.tag == qn("w:br") and element.get(qn("w:type"), "textWrapping") == "textWrapping"
    ):
        return "\n"
    return "".join(_revision_text(child, accept) for child in element)


def _source_runs(para):
    # Generated changes target plain paragraphs. Refuse to silently discard fields,
    # bookmarks, drawings or hyperlinks in a manually edited complex paragraph.
    if any(child.tag not in {qn("w:pPr"), qn("w:r"), qn("w:proofErr")} for child in para._p):
        raise RenderError("改动段落含链接或书签等复杂内容，无法安全生成修订对照。")
    result, offset = [], 0
    allowed = {qn(tag) for tag in ("w:rPr", "w:t", "w:tab", "w:br", "w:cr")}
    for run in para.runs:
        if any(child.tag not in allowed for child in run._r):
            raise RenderError("改动段落含域或图形等复杂内容，无法安全生成修订对照。")
        result.append((offset, offset + len(run.text), copy.deepcopy(run._r)))
        offset += len(run.text)
    if offset != len(para.text):
        raise RenderError("无法完整读取改动段落，已停止生成修订对照。")
    return result


def _slice_runs(segments, start, end, para, deleted=False):
    for left, right, element in segments:
        if right <= start or left >= end:
            continue
        run = Run(copy.deepcopy(element), para)
        run.text = run.text[max(0, start - left) : min(right, end) - left]
        if deleted:
            for text in run._r.findall(qn("w:t")):
                text.tag = qn("w:delText")
        yield run._r


def _set_revision_display(doc):
    settings = doc.settings.element
    view = settings.find(qn("w:revisionView"))
    if view is None:
        # CT_Settings ordering: revisionView follows mailMerge and precedes trackRevisions.
        preceding = {
            qn("w:" + name)
            for name in (
                "writeProtection view zoom removePersonalInformation removeDateAndTime "
                "doNotDisplayPageBoundaries displayBackgroundShape printPostScriptOverText "
                "printFractionalCharacterWidth printFormsData embedTrueTypeFonts "
                "embedSystemFonts saveSubsetFonts saveFormsData mirrorMargins alignBordersAndEdges "
                "bordersDoNotSurroundHeader bordersDoNotSurroundFooter gutterAtTop hideSpellingErrors "
                "hideGrammaticalErrors activeWritingStyle proofState formsDesign attachedTemplate "
                "linkStyles stylePaneFormatFilter stylePaneSortMethod documentType mailMerge"
            ).split()
        }
        index = max((i + 1 for i, node in enumerate(settings) if node.tag in preceding), default=0)
        view = OxmlElement("w:revisionView")
        settings.insert(index, view)
    for name in ("markup", "comments", "insDel"):
        view.set(qn("w:" + name), "true")
    track = settings.find(qn("w:trackRevisions"))
    if track is None:
        track = OxmlElement("w:trackRevisions")
        view.addnext(track)
    track.set(qn("w:val"), "true")


def _risk_comment(doc, para, risk, location):
    label = {
        "pending": "待核实",
        "reverted": "已拦截并回退",
        "resolved": "已处理并复核",
        "deleted": "已删除",
    }[risk.status]
    comment = doc.comments.add_comment(
        text=(
            f"{risk.id}｜{label}｜修订对照中的风险提示\n位置：{location}\n"
            f"问题表述：{risk.quote}\n原文：{risk.source_text}\n"
            f"原因：{risk.reason}\n建议：{risk.suggestion}"
        ),
        author="Resume Studio",
        initials="RS",
    )
    # Keep anchors outside ins/del so accepting/rejecting a revision does not erase the warning.
    start, end = OxmlElement("w:commentRangeStart"), OxmlElement("w:commentRangeEnd")
    for node in (start, end):
        node.set(qn("w:id"), str(comment.comment_id))
    para._p.insert(1 if para._p.pPr is not None else 0, start)
    para._p.append(end)
    reference = OxmlElement("w:commentReference")
    reference.set(qn("w:id"), str(comment.comment_id))
    run = para.add_run()
    style = OxmlElement("w:rStyle")
    style.set(qn("w:val"), "CommentReference")
    run._r.get_or_add_rPr().append(style)
    run._r.append(reference)


def render_comparison(source: bytes, final_text: dict[str, str], risks=()) -> bytes:
    doc = load_document(source)
    paragraphs = paragraph_map(doc)
    locations = {
        f"b{i:03d}": path
        for i, (path, _) in enumerate(
            item for item in document_paragraphs(doc) if item[1].text.strip()
        )
    }
    if set(final_text) != set(paragraphs):
        raise RenderError("修订对照的原文位置与最终正文不匹配。")
    original = {key: para.text for key, para in paragraphs.items()}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    revision_id = 0
    for key, para in paragraphs.items():
        before, after = original[key], final_text[key]
        if before == after:
            continue
        segments = _source_runs(para)
        base = max(para.runs, key=lambda r: len(r.text), default=None)
        props = copy.deepcopy(base._r.rPr) if base is not None and base._r.rPr is not None else None
        old, new = _tokens(before), _tokens(after)
        if "".join(old) != before or "".join(new) != after:
            raise RenderError("修订差异拆分未完整保留原文字符。")
        offsets = [0]
        for token in old:
            offsets.append(offsets[-1] + len(token))
        para.clear()  # Paragraph styles, indentation and table location remain intact.
        for operation, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if operation == "equal":
                for node in _slice_runs(segments, offsets[a], offsets[b], para):
                    para._p.append(node)
                continue
            for kind in ("del", "ins"):
                if kind == "del" and a == b or kind == "ins" and c == d:
                    continue
                revision = OxmlElement("w:" + kind)
                revision.set(qn("w:id"), str(revision_id))
                revision.set(qn("w:author"), "Resume Studio")
                revision.set(qn("w:date"), stamp)
                revision_id += 1
                if kind == "del":
                    for node in _slice_runs(segments, offsets[a], offsets[b], para, deleted=True):
                        revision.append(node)
                else:
                    run = Run(OxmlElement("w:r"), para)
                    if props is not None:
                        run._r.append(copy.deepcopy(props))
                    run.text = "".join(new[c:d])
                    revision.append(run._r)
                para._p.append(revision)
    for risk in risks:
        if risk.block_id not in paragraphs:
            raise RenderError("风险无法定位到修订对照文档。")
        para = paragraphs[risk.block_id]
        location = locations[risk.block_id]
        if not location.startswith("body/"):
            # Word forbids header/footer comment anchors; locate these explicitly in a body comment.
            para = next(p for key, p in paragraphs.items() if locations[key].startswith("body/"))
            location = "页眉/页脚（风险批注挂在正文开头）：" + location
        _risk_comment(doc, para, risk, location)
    _set_revision_display(doc)
    out = BytesIO()
    doc.save(out)
    result = out.getvalue()
    reread = {path: p for path, p in document_paragraphs(Document(BytesIO(result)))}
    for key, location in locations.items():
        para = reread[location]
        if _revision_text(para._p, accept=True) != final_text[key]:
            raise RenderError("接受全部修订后的正文与最终改写不一致。")
        if _revision_text(para._p, accept=False) != original[key]:
            raise RenderError("拒绝全部修订后无法还原原文。")
    return result


def export_saved_comparison(run_dir: Path) -> Path:
    """Backfill a saved run without changing its resume, analysis, or using any API."""
    run_dir = Path(run_dir)
    data = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    result = render_comparison(
        (run_dir / "source_resume.docx").read_bytes(),
        data["final_text"],
        [Risk.model_validate(r) for r in data["risks"]],
    )
    target = run_dir / FILENAME
    temp = run_dir / ("." + uuid.uuid4().hex + ".docx")
    try:
        temp.write_bytes(result)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="为已有结果导出 Word 修订对照版（不调用 AI）")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(export_saved_comparison(args.run_dir))
