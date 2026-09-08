from .schemas import RunResult


def quote(text):
    return "\n".join("> " + line for line in (text or "（空）").splitlines())


def comments_markdown(result: RunResult, job, resume):
    pending = [r for r in result.risks if r.status == "pending"]
    lines = [
        f"# 简历调整说明：{job.company} — {job.title}",
        "",
        f"运行：`{result.run_id}` · {result.provider} / {result.model}",
        "",
        f"**待核实 {len(pending)} 项；已处理 {len(result.risks) - len(pending)} 项。**",
        "",
        "事实审校：" + ("已完成（不代表外部背景核实）" if result.audit_complete else "未完成"),
        "",
        result.layout["message"],
        "",
        "原位置改写对照见 `resume_comparison.docx`：在 Word 中选择“审阅 → 所有标记”，"
        "查看真实插入/删除修订，可逐处接受或拒绝。风险批注仍需核实，接受修订不等于事实已核实。",
        "",
    ]
    if result.audit_error:
        lines += ["审校未完成原因：" + result.audit_error, ""]
    if not result.clean_available:
        lines += ["当前提供带标注的审阅版；未生成无标注简历。", ""]
    lines += ["## 风险与处理记录", ""]
    if not result.risks:
        lines += ["本次检查未发现具体事实风险；请核对最终文件。", ""]
    for r in sorted(result.risks, key=lambda r: r.status != "pending"):
        state = {
            "pending": "待核实",
            "reverted": "已拦截/回退",
            "resolved": "已处理并复核",
            "deleted": "已删除",
        }[r.status]
        lines += [
            f"### {r.id} · {state} · {r.block_id}",
            "",
            f"来源：{r.origin}",
            "",
            "问题表述：",
            quote(r.quote),
            "",
            "原文依据：",
            quote(r.source_text),
            "",
            "原因：" + r.reason,
            "",
            "建议：" + r.suggestion,
            "",
        ]
    lines += ["## 职位定位建议", "", result.analysis.positioning, ""]
    lines += [f"- {p}" for p in result.analysis.priorities] + ["", "## 实际修改", ""]
    blocks = {b.id: b for b in resume.blocks}
    actual = 0
    for c in result.changes:
        final = result.final_text[c.block_id]
        if final == blocks[c.block_id].text:
            continue
        actual += 1
        lines += [
            f"### {c.block_id}",
            "",
            "修改前：",
            quote(c.before),
            "",
            "修改后：",
            quote(final),
            "",
            "修改理由：" + c.reason,
            "",
            "依据：" + ", ".join(c.evidence_ids),
            "",
            "职位要求：" + ", ".join(c.requirement_ids),
            "",
        ]
    if not actual:
        lines += ["本次未保留对原文的修改。", ""]
    lines += ["## 职位要求与证据", ""]
    requirements = {r.id: r for r in job.requirements}
    names = {
        "direct": "直接证据",
        "transferable": "可迁移经验",
        "missing": "未找到证据",
        "conflict": "信息冲突",
    }
    for m in result.analysis.mappings:
        lines += [
            f"- **{names[m.status]}** · {requirements[m.requirement_id].text}：{m.reason}"
            + (f"（{', '.join(m.evidence_ids)}）" if m.evidence_ids else "")
        ]
    lines += ["", "## 建议补充或核实", ""]
    lines += [f"- {q}" for q in result.analysis.questions + job.warnings]
    return "\n".join(lines) + "\n"
