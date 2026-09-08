import re
from collections import Counter
from datetime import date

from .schemas import Analysis, Audit, Draft, Risk


class ContractError(ValueError):
    pass


def check_analysis(analysis: Analysis, resume, job):
    ids = {r.id for r in job.requirements}
    if Counter(m.requirement_id for m in analysis.mappings) != Counter(ids):
        raise ContractError("职位分析未覆盖全部要求或存在重复 ID。")
    blocks = {b.id for b in resume.blocks if not b.private}
    for m in analysis.mappings:
        if not set(m.evidence_ids) <= blocks:
            raise ContractError("职位分析引用了不存在或不可用的证据。")
        if m.status in ("direct", "transferable") and not m.evidence_ids:
            raise ContractError("匹配结论缺少证据。")


def check_draft(draft: Draft, resume, job):
    blocks = {b.id: b for b in resume.blocks}
    reqs = {r.id for r in job.requirements}
    seen = set()
    for c in draft.changes:
        if c.block_id not in blocks or c.block_id in seen:
            raise ContractError("改写包含不存在或重复的文本块。")
        seen.add(c.block_id)
        b = blocks[c.block_id]
        if c.before != b.text or not b.editable:
            raise ContractError("改写试图修改锁定内容，或原文锚点不匹配。")
        if not c.after.strip() or any(x in c.after for x in ("\n", "\r", "\t")):
            raise ContractError("改写正文为空或包含不支持的换行/制表符。")
        if not c.evidence_ids or not set(c.evidence_ids) <= blocks.keys():
            raise ContractError("改写缺少有效证据。")
        if any(blocks[x].private for x in c.evidence_ids):
            raise ContractError("改写引用了不可用的隐私文本块。")
        if not set(c.requirement_ids) <= reqs:
            raise ContractError("改写引用了不存在的职位要求。")


def check_audit(audit: Audit, final_text, review_ids):
    if Counter(v.block_id for v in audit.verdicts) != Counter(review_ids):
        raise ContractError("审校未覆盖全部正文，或含重复/不存在的文本块。")
    for v in audit.verdicts:
        if v.severity != "clear" and (
            not v.quote or v.quote not in final_text[v.block_id] or not v.reason.strip()
        ):
            raise ContractError("审校风险无法定位到最终正文或缺少原因。")


def numbers(text):
    # Semantic changes to numbers are conservatively rejected; words handled by audit.
    return Counter(re.findall(r"(?<!\w)\$?\d[\d,]*(?:\.\d+)?(?:\+|%|[BMK](?!\w))?", text))


def rule_risks(resume, final_text, supplements=None, today=None):
    supplements = supplements or {}
    today = today or date.today()
    risks = []
    for b in resume.blocks:
        text = final_text[b.id]
        if b.private or not text.strip():
            continue
        source = b.text
        extra = supplements.get(b.id, "")
        evidence = source + "\n" + extra

        def add(severity, origin, quote, reason, suggestion, block_id=b.id, source_text=source):
            risks.append(
                Risk(
                    block_id=block_id,
                    severity=severity,
                    origin=origin,
                    quote=quote,
                    source_text=source_text,
                    reason=reason,
                    suggestion=suggestion,
                )
            )

        if text != source:
            if not extra and numbers(text) != numbers(source):
                add(
                    "violation",
                    "rewrite",
                    text,
                    "量化数字、范围或单位与该段原文不一致。",
                    "恢复原文数字及其含义。",
                )
            if re.search(r"approval\s+rates?", evidence, re.IGNORECASE) and re.search(
                r"approval\s+(velocity|speed)", text, re.IGNORECASE
            ):
                add(
                    "violation",
                    "rewrite",
                    text,
                    "将审批通过率改成了审批速度，指标含义发生变化。",
                    "保留 approval rates。",
                )
            if re.search(
                r"\b(in progress|expected|pursuing)\b", source, re.IGNORECASE
            ) and re.search(r"certif", source, re.IGNORECASE):
                if not extra and not re.search(
                    r"\b(in progress|expected|pursuing)\b", text, re.IGNORECASE
                ):
                    add(
                        "violation",
                        "rewrite",
                        text,
                        "删除了证书进行中/预计完成的状态。",
                        "恢复原状态，或补充实际取得情况。",
                    )
            # A small explicit regression guard; the semantic audit covers unlisted concepts.
            for term in ("SQL", "DCF", "NPV", "Tableau", "Power BI", "WorkBoard", "MS Project"):
                if re.search(
                    r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE
                ) and not re.search(r"\b" + re.escape(term) + r"\b", evidence, re.IGNORECASE):
                    add(
                        "violation",
                        "rewrite",
                        term,
                        f"该段证据未记载 {term}，不能由职位要求推导。",
                        "删除该技能或补充实际使用经历。",
                    )
            if len(text) > max(len(source) * 1.3, len(source) + 100):
                add(
                    "suspected",
                    "rewrite",
                    text,
                    "改写显著变长，可能引入额外声明并影响排版。",
                    "核查新增信息并压缩。",
                )
        if re.search(r"certif", text, re.IGNORECASE):
            match = re.search(r"expected\s+([A-Za-z]+)\s+(20\d{2})", text, re.IGNORECASE)
            if match:
                months = [
                    "jan",
                    "feb",
                    "mar",
                    "apr",
                    "may",
                    "jun",
                    "jul",
                    "aug",
                    "sep",
                    "oct",
                    "nov",
                    "dec",
                ]
                month = match[1][:3].lower()
                if month in months and (int(match[2]), months.index(month) + 1) < (
                    today.year,
                    today.month,
                ):
                    add(
                        "suspected",
                        "source" if match[0] in source else "rewrite",
                        match[0],
                        "证书预计完成日期已过，实际状态仍未明确。",
                        "填写已取得、仍在进行或已停止，以及实际/新的预计日期。",
                    )
    return risks


def audit_risks(audit, resume, final_text):
    blocks = {b.id: b for b in resume.blocks}
    risks = []
    for v in audit.verdicts:
        if v.severity == "clear":
            continue
        # A model cannot label an unchanged claim as a new rewrite to auto-dismiss it.
        origin = "source" if final_text[v.block_id] == blocks[v.block_id].text else v.origin
        risks.append(
            Risk(
                block_id=v.block_id,
                severity=v.severity,
                origin=origin,
                quote=v.quote,
                source_text=blocks[v.block_id].text,
                reason=v.reason,
                suggestion=v.suggestion,
            )
        )
    return risks
