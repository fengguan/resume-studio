import hashlib
import json
import re
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from .schemas import Block, JobSpec, Requirement, Resume

MAX_DOCX_BYTES = 10 * 1024 * 1024
MAX_XML_BYTES = 40 * 1024 * 1024


class InputError(ValueError):
    pass


def candidate_location(resume):
    """Expose only a city/state prefix, never a street address or contact fields."""
    for block in resume.blocks:
        if not block.private or "|" not in block.text:
            continue
        prefix = block.text.split("|", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z .'-]+,\s*[A-Z]{2}(?:\s*\([^\d@]+\))?", prefix):
            return prefix
    return "未提取到明确城市/地区，请根据职位地点要求提示用户核实。"


def walk_paragraphs(parent, prefix="body"):
    """Traverse actual XML cells once (merged cells are not duplicated)."""
    for i, child in enumerate(parent.iter_inner_content()):
        path = f"{prefix}/{i}"
        if isinstance(child, Paragraph):
            yield path, child
        elif isinstance(child, Table):
            seen = set()
            for ri, row in enumerate(child.rows):
                for ci, cell in enumerate(row.cells):
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    yield from walk_paragraphs(cell, f"{path}/row{ri}/cell{ci}")


def document_paragraphs(doc):
    yield from walk_paragraphs(doc)
    seen = set()
    for i, section in enumerate(doc.sections):
        for kind in (
            "header",
            "footer",
            "first_page_header",
            "first_page_footer",
            "even_page_header",
            "even_page_footer",
        ):
            part = getattr(section, kind)
            # Do not create absent header/footer parts just by reading them.
            if part.is_linked_to_previous:
                continue
            if part.part.partname in seen:
                continue
            seen.add(part.part.partname)
            yield from walk_paragraphs(part, f"section{i}/{kind}")


def load_document(data: bytes):
    if not data or len(data) > MAX_DOCX_BYTES:
        raise InputError("DOCX 为空或超过 10 MB。")
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(x.file_size for x in archive.infolist()) > MAX_XML_BYTES:
                raise InputError("DOCX 解压后过大，请使用简洁的文字简历。")
            if "word/document.xml" not in archive.namelist():
                raise InputError("文件不是有效的 DOCX。")
            for name in archive.namelist():
                if not (name.startswith("word/") and name.endswith(".xml")):
                    continue
                root = etree.fromstring(
                    archive.read(name), etree.XMLParser(resolve_entities=False, no_network=True)
                )
                unsupported = root.xpath(
                    "//*[local-name()='txbxContent' or local-name()='ins' or "
                    "local-name()='del' or local-name()='altChunk' or "
                    "local-name()='sdt' or local-name()='object' or "
                    "local-name()='footnoteReference' or local-name()='endnoteReference']"
                )
                if unsupported:
                    raise InputError(
                        "检测到文本框、修订、内容控件或嵌入内容。请先接受修订并转为普通段落/表格。"
                    )
                if root.xpath("//*[local-name()='cols' and @*[local-name()='num'] > 1]"):
                    raise InputError("暂不支持多栏排版，请先转为单栏或表格布局。")
                if name == "word/comments.xml":
                    raise InputError("输入已有 Word 批注，请先在副本中删除批注后使用。")
        return Document(BytesIO(data))
    except InputError:
        raise
    except (BadZipFile, KeyError, ValueError, etree.XMLSyntaxError, OSError) as exc:
        raise InputError("无法读取 DOCX；文件可能损坏或已加密。") from exc


def parse_resume(data: bytes) -> Resume:
    doc = load_document(data)
    blocks = []
    for location, para in document_paragraphs(doc):
        text = para.text
        if not text.strip():
            continue
        phones = re.findall(r"\+?\d[\d ()-]{8,}\d", text)
        private = bool(re.search(r"[\w.+-]+@[\w.-]+|linkedin\.com", text)) or any(
            len(re.sub(r"\D", "", phone)) >= 10 for phone in phones
        )
        heading = (text.isupper() and " | " not in text) or (
            para.style and para.style.name.startswith("Heading")
        )
        dated = bool(re.search(r"\b(?:19|20)\d{2}\b", text)) and len(text) < 160
        repeated_name = bool(blocks) and (
            text == blocks[0].text or text.startswith(blocks[0].text + " |")
        )
        identity = len(blocks) == 0 or private or repeated_name
        # Keep mixed runs, fields, dates, headings and table labels intact.
        complex_content = bool(
            para.hyperlinks or para._p.xpath(".//w:br|.//w:fldChar|.//w:drawing|.//w:fldSimple")
        )
        editable = (
            location.startswith("body")
            and not any((identity, heading, dated, complex_content))
            and (len(text) >= 110 or " | " in text)
        )
        blocks.append(
            Block(
                id=f"b{len(blocks):03d}",
                text=text,
                location=location,
                editable=editable,
                private=identity,
            )
        )
    if len(blocks) < 3 or sum(len(b.text) for b in blocks) < 100:
        raise InputError("未读取到足够的简历正文；图片简历暂不支持。")
    if sum(len(b.text) for b in blocks) > 80000:
        raise InputError("简历正文过长，请控制在 80,000 字符以内。")
    return Resume(sha256=hashlib.sha256(data).hexdigest(), blocks=blocks)


def parse_job(data: bytes) -> JobSpec:
    if len(data) > 1024 * 1024:
        raise InputError("职位 JSON 超过 1 MB。")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise InputError("职位文件不是有效的 UTF-8 JSON。") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("job"), dict):
        raise InputError("职位 JSON 需要 job 对象，包含 title 和 company。")
    job = raw["job"]
    if not all(isinstance(job.get(k), str) and job[k].strip() for k in ("title", "company")):
        raise InputError("job.title 和 job.company 必须是非空文字。")
    requirements = []

    def add(items, path, category):
        if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
            raise InputError(f"{path} 必须是文字数组。")
        for i, item in enumerate(items):
            if item.strip():
                requirements.append(
                    Requirement(
                        id=f"j{len(requirements):03d}",
                        text=item,
                        path=f"{path}[{i}]",
                        category=category,
                    )
                )

    for name in ("responsibilities", "skills"):
        add(raw.get(name, []), name, name)
    quals = raw.get("qualifications", {})
    if not isinstance(quals, dict):
        raise InputError("qualifications 必须是对象。")
    for name in ("required", "preferred", "core_competencies"):
        add(quals.get(name, []), f"qualifications.{name}", name)
    if not requirements:
        raise InputError("职位 JSON 至少需要一条职责、技能或资格要求。")
    if sum(len(r.text) for r in requirements) > 60000:
        raise InputError("职位要求过长，请控制在 60,000 字符以内。")
    context = {
        k: job[k]
        for k in (
            "location",
            "location_requirement",
            "workplace_type",
            "experience_required",
            "h1b_sponsorship",
        )
        if k in job
    }
    context["benefits"] = raw.get("benefits", [])
    warnings = (
        [x for x in raw.get("flags", []) if isinstance(x, str)]
        if isinstance(raw.get("flags", []), list)
        else []
    )
    return JobSpec(
        title=job["title"],
        company=job["company"],
        summary=str(job.get("summary", "")),
        requirements=requirements,
        context=context,
        warnings=warnings,
    )
