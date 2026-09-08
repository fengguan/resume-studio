import hashlib
import json
import os
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from . import prompts
from .llm import ModelError
from .parsing import candidate_location, parse_job, parse_resume
from .rendering import check_layout, render_resume
from .reporting import comments_markdown
from .revisions import render_comparison
from .schemas import Analysis, Audit, Change, Draft, Risk, RunResult
from .validation import (
    ContractError,
    audit_risks,
    check_analysis,
    check_audit,
    check_draft,
    rule_risks,
)


def dump(path, value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def model_blocks(resume, final_text=None):
    return [
        {
            "id": b.id,
            "text": (
                "[本地保留的身份/联系信息]" if b.private else (final_text or {}).get(b.id, b.text)
            ),
            "editable": b.editable and not b.private,
        }
        for b in resume.blocks
    ]


def dedupe_risks(risks):
    seen, output = set(), []
    for r in risks:
        key = (r.block_id, r.quote, r.origin, r.status)
        if key not in seen:
            seen.add(key)
            output.append(r)
    for i, r in enumerate(output):
        r.id = f"R-{i + 1:03d}"
    return output


def run_pipeline(
    resume_bytes,
    job_bytes,
    client,
    output_root="outputs",
    progress=None,
    target_pages=2,
    previous=None,
    edits=None,
    supplements=None,
):
    """Generate or revise; revisions are re-audited and saved as immutable new runs."""
    progress = progress or (lambda text: None)
    progress("读取简历与职位")
    resume, job = parse_resume(resume_bytes), parse_job(job_bytes)
    originals = {b.id: b.text for b in resume.blocks}
    blocks = {b.id: b for b in resume.blocks}
    supplements = dict(supplements or {})
    if any(
        k not in blocks or blocks[k].private or not isinstance(v, str)
        for k, v in supplements.items()
    ):
        raise ContractError("补充事实的位置无效。")
    if sum(len(v) for v in supplements.values()) > 20000:
        raise ContractError("补充事实过长，请控制在 20,000 字符以内。")
    if previous:
        saved = Path(previous.output_dir)
        if hashlib.sha256((saved / "source_resume.docx").read_bytes()).hexdigest() != resume.sha256:
            raise ContractError("审阅结果与本次原始简历不匹配。")
        if (saved / "job.json").read_bytes() != job_bytes:
            raise ContractError("审阅结果与本次职位不匹配。")
        old_supplements = json.loads((saved / "supplements.json").read_text())
        supplements = old_supplements | supplements
        pending_ids = {r.block_id for r in previous.risks if r.status == "pending"}
        for key, text in (edits or {}).items():
            if key not in pending_ids or blocks[key].private:
                raise ContractError("只能处理当前待核实的非隐私文本块。")
            if (
                not isinstance(text, str)
                or any(c in text for c in ("\n", "\r", "\t"))
                or len(text) > 4000
            ):
                raise ContractError("修订正文须为不超过 4,000 字符的单段文字。")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    work = root / ("." + run_id + ".partial")
    destination = root / run_id
    work.mkdir(mode=0o700)
    started = time.monotonic()
    usage_start = len(client.usage)
    metadata = {
        "run_id": run_id,
        "source_hash": resume.sha256,
        "job_hash": hashlib.sha256(job_bytes).hexdigest(),
        "provider": client.config.provider,
        "model": client.config.model,
        "prompt_version": prompts.VERSION,
        "schema_version": 1,
        "target_pages": target_pages,
        "status": "running",
        "parent_run_id": previous.run_id if previous else None,
    }
    dump(work / "metadata.json", metadata)
    try:
        (work / "source_resume.docx").write_bytes(resume_bytes)
        (work / "job.json").write_bytes(job_bytes)
        dump(work / "resume.json", resume)
        dump(work / "job_spec.json", job)
        dump(work / "supplements.json", supplements)
        base = {
            "today": date.today().isoformat(),
            "resume": model_blocks(resume),
            "candidate_location": candidate_location(resume),
            "job": job.model_dump(),
            "user_facts": supplements,
        }
        progress("分析职位要求与原始证据")
        analysis = client.generate("analysis", prompts.ANALYZE, base, Analysis)
        check_analysis(analysis, resume, job)
        dump(work / "analysis.json", analysis)
        if previous:
            final_text = previous.final_text | (edits or {})
            changes = {c.block_id: c.model_copy(deep=True) for c in previous.changes}
            for key, text in (edits or {}).items():
                changes[key] = Change(
                    block_id=key,
                    before=originals[key],
                    after=text,
                    evidence_ids=[key],
                    requirement_ids=[],
                    reason="用户处理待核实内容；已重新执行事实审校。",
                )
            draft = Draft(changes=list(changes.values()))
        else:
            progress("根据证据改写简历")
            draft = client.generate(
                "tailoring", prompts.TAILOR, base | {"analysis": analysis.model_dump()}, Draft
            )
            check_draft(draft, resume, job)
            final_text = originals | {c.block_id: c.after for c in draft.changes}
        dump(work / "proposed_changes.json", draft)
        history = []
        if previous:
            for risk in previous.risks:
                r = risk.model_copy(deep=True)
                # Historical pending risks are resolved only after a complete fresh audit.
                if r.status != "pending":
                    history.append(r)
        progress("检查数字、指标含义和证书状态")
        initial_rules = rule_risks(resume, final_text, supplements)
        blocked_ids = {
            r.block_id for r in initial_rules if r.severity == "violation" and r.origin == "rewrite"
        }
        for r in initial_rules:
            if r.block_id in blocked_ids:
                r.status = "reverted"
                history.append(r)
        for block_id in blocked_ids:
            final_text[block_id] = originals[block_id]

        audit_complete, audit_error, semantic = False, "", []
        for iteration in range(2):
            progress("独立审校全部正文" if not iteration else "复核回退后的最终正文")
            review_ids = [b.id for b in resume.blocks if not b.private and final_text[b.id].strip()]
            try:
                audit = client.generate(
                    "audit",
                    prompts.AUDIT,
                    base
                    | {
                        "candidate": model_blocks(resume, final_text),
                        "review_block_ids": review_ids,
                    },
                    Audit,
                )
                check_audit(audit, final_text, review_ids)
                dump(work / f"audit_{iteration + 1}.json", audit)
                semantic = audit_risks(audit, resume, final_text)
                blocked = {
                    r.block_id
                    for r in semantic
                    if r.severity == "violation"
                    and r.origin == "rewrite"
                    and final_text[r.block_id] != originals[r.block_id]
                }
                if not blocked:
                    audit_complete = True
                    break
                for r in semantic:
                    if r.block_id in blocked:
                        r.status = "reverted"
                        history.append(r)
                for block_id in blocked:
                    final_text[block_id] = originals[block_id]
                semantic = []
                if iteration == 1:
                    audit_error = "已达到有限回退次数；最后回退后的全文尚未完成复核。"
            except (ModelError, ContractError) as exc:
                audit_error = str(exc)
                semantic = []
                break
        risks = history + rule_risks(resume, final_text, supplements) + semantic
        if previous:
            current_pending = {r.block_id for r in risks if r.status == "pending"}
            for prior in previous.risks:
                if prior.status != "pending":
                    continue
                if audit_complete and prior.block_id not in current_pending:
                    r = prior.model_copy(deep=True)
                    r.status = "deleted" if not final_text[r.block_id].strip() else "resolved"
                    risks.append(r)
                elif not audit_complete:
                    risks.append(prior.model_copy(deep=True))
        if not audit_complete:
            anchor = next(b for b in resume.blocks if not b.private and final_text[b.id].strip())
            risks.append(
                Risk(
                    block_id=anchor.id,
                    severity="suspected",
                    origin="system",
                    quote=final_text[anchor.id],
                    source_text=anchor.text,
                    reason="全文事实检查未完成：" + audit_error,
                    suggestion="重新执行审校；当前文件仅供审阅。",
                )
            )
        risks = dedupe_risks(risks)
        clean_available = audit_complete and not any(r.status == "pending" for r in risks)
        progress("生成 Word 批注与检查文件")
        plain_bytes = render_resume(resume_bytes, final_text, [], annotate=False)
        review_bytes = render_resume(resume_bytes, final_text, risks, annotate=True)
        comparison_bytes = render_comparison(resume_bytes, final_text, risks)
        layout = check_layout(plain_bytes, target_pages)
        (work / "resume_review.docx").write_bytes(review_bytes)
        (work / "resume_comparison.docx").write_bytes(comparison_bytes)
        if clean_available:
            (work / "tailored_resume.docx").write_bytes(plain_bytes)
        result = RunResult(
            run_id=run_id,
            output_dir=str(destination),
            analysis=analysis,
            changes=draft.changes,
            risks=risks,
            final_text=final_text,
            audit_complete=audit_complete,
            audit_error=audit_error,
            layout=layout,
            clean_available=clean_available,
            provider=client.config.provider,
            model=client.config.model,
            usage=client.usage[usage_start:],
        )
        (work / "comments.md").write_text(comments_markdown(result, job, resume), encoding="utf-8")
        dump(
            work / "changes.json",
            [
                {"block_id": b.id, "before": b.text, "after": final_text[b.id]}
                for b in resume.blocks
                if b.text != final_text[b.id]
            ],
        )
        dump(
            work / "validation.json",
            {
                "audit_complete": audit_complete,
                "audit_error": audit_error,
                "risks": [r.model_dump() for r in risks],
                "layout": layout,
            },
        )
        dump(work / "result.json", result)
        metadata.update(
            status="complete" if clean_available else "needs_review",
            seconds=round(time.monotonic() - started, 2),
            usage=result.usage,
        )
        dump(work / "metadata.json", metadata)
        # One rename publishes a complete run; no existing successful output is overwritten.
        os.rename(work, destination)
        progress(
            "完成："
            + ("已提供无标注版与审阅版" if clean_available else "存在待核实项目，请查看审阅版")
        )
        return result
    except Exception:
        metadata.update(status="failed", seconds=round(time.monotonic() - started, 2))
        dump(work / "metadata.json", metadata)
        os.rename(work, root / (run_id + ".failed"))
        raise
