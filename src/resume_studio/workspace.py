"""Durable local drafts and paragraph conversations, separate from audited exports."""

import json
import uuid
from datetime import date
from pathlib import Path

from filelock import FileLock

from . import prompts
from .llm import ModelError
from .parsing import candidate_location, load_document, parse_job, parse_resume
from .pipeline import model_blocks, run_pipeline
from .rendering import RenderError, paragraph_map
from .revisions import _source_runs
from .schemas import Audit, Refinement, ReviewEvent, RunResult, Workspace
from .validation import ContractError, audit_risks, check_audit, rule_risks


class StaleDraft(ValueError):
    pass


def load_result(path: Path) -> RunResult:
    path = Path(path).resolve()
    result = RunResult.model_validate_json((path / "result.json").read_text(encoding="utf-8"))
    return result.model_copy(update={"output_dir": str(path)})


class ReviewWorkspace:
    def __init__(self, result: RunResult):
        self.result = result
        self.root = Path(result.output_dir)
        self.path = self.root / "workspace.json"
        self.lock = FileLock(str(self.root / ".workspace.lock"), timeout=10)
        self.source = (self.root / "source_resume.docx").read_bytes()
        self.job_bytes = (self.root / "job.json").read_bytes()
        self.resume, self.job = parse_resume(self.source), parse_job(self.job_bytes)
        self.blocks = {b.id: b for b in self.resume.blocks}
        if self.blocks.keys() != result.final_text.keys():
            raise ValueError("已保存结果与原文位置不一致，不能打开交互审阅。")
        self.base_facts = json.loads((self.root / "supplements.json").read_text(encoding="utf-8"))
        pending = {
            r.block_id for r in result.risks if r.status == "pending" and r.origin != "system"
        }
        self.editable = set()
        paras = paragraph_map(load_document(self.source))
        for b in self.resume.blocks:
            if not b.private and (b.editable or b.id in pending):
                try:
                    _source_runs(paras[b.id])
                    self.editable.add(b.id)
                except RenderError:
                    pass

    def _read(self):
        if not self.path.exists():
            return Workspace(
                run_id=self.result.run_id,
                source_hash=self.resume.sha256,
                texts=dict(self.result.final_text),
                supplements=dict(self.base_facts),
            )
        state = Workspace.model_validate_json(self.path.read_text(encoding="utf-8"))
        if (
            state.run_id != self.result.run_id
            or state.source_hash != self.resume.sha256
            or state.texts.keys() != self.blocks.keys()
        ):
            raise ValueError("草稿与原始文件不匹配，请重新打开对应结果。")
        return state

    def _write(self, state):
        temp = self.root / (".workspace-" + uuid.uuid4().hex + ".json")
        try:
            temp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)

    def load(self):
        with self.lock:
            return self._read()

    def dirty(self, state):
        return state.texts != self.result.final_text or {
            k: v for k, v in state.supplements.items() if v
        } != {k: v for k, v in self.base_facts.items() if v}

    def _validate(self, key, text, facts):
        if key not in self.editable:
            raise ValueError("这段是锁定信息或复杂内容，当前不支持在预览中改写。")
        if not isinstance(text, str) or len(text) > 4000 or any(c in text for c in "\n\r\t"):
            raise ValueError("请保持单个段落（不含回车或制表符），最多 4,000 字符。")
        if not isinstance(facts, str) or len(facts) > 4000:
            raise ValueError("补充事实最多 4,000 字符。")

    def _edit(self, state, key, text, facts, origin):
        self._validate(key, text, facts)
        before, before_facts = state.texts[key], state.supplements.get(key, "")
        if text == before and facts == before_facts:
            return
        state.undo.setdefault(key, []).append({"text": before, "facts": before_facts})
        state.texts[key] = text
        if facts or key in state.supplements:
            state.supplements[key] = facts
        state.checks[key] = {"status": "manual", "issues": []}
        state.history.append(
            {
                "block_id": key,
                "before": before,
                "after": text,
                "origin": origin,
                "version": state.version + 1,
            }
        )

    def _start(self, event):
        state = self._read()
        if event.id in state.requests:
            return state, False
        if event.version != state.version:
            raise StaleDraft("草稿已在另一个窗口更新。已载入最新草稿并保留当前输入，请核对后重试。")
        return state, True

    def local(self, event: ReviewEvent):
        with self.lock:
            state, fresh = self._start(event)
            if not fresh:
                return state
            if event.action in ("save", "restore"):
                text = (
                    self.blocks[event.block_id].text
                    if event.action == "restore" and event.block_id in self.blocks
                    else event.text
                )
                self._edit(state, event.block_id, text, event.facts, event.action)
            elif event.action == "undo":
                self._validate(event.block_id, state.texts.get(event.block_id, ""), "")
                stack = state.undo.get(event.block_id, [])
                if not stack:
                    raise ValueError("这个段落没有可撤销的修改。")
                old = stack.pop()
                state.history.append(
                    {
                        "block_id": event.block_id,
                        "before": state.texts[event.block_id],
                        "after": old["text"],
                        "origin": "undo",
                        "version": state.version + 1,
                    }
                )
                state.texts[event.block_id], state.supplements[event.block_id] = (
                    old["text"],
                    old["facts"],
                )
                state.checks[event.block_id] = {"status": "manual", "issues": []}
            else:
                raise ValueError("未知的本地编辑操作。")
            if sum(len(v) for v in state.supplements.values()) > 20000:
                raise ValueError("补充事实总长度超过 20,000 字符。")
            state.version += 1
            state.requests[event.id] = {
                "status": "complete",
                "message": "已保存本地草稿，预览已更新。",
            }
            self._write(state)
            return state

    def begin(self, event):
        with self.lock:
            state, fresh = self._start(event)
            if not fresh:
                return state, False
            if event.action == "ask":
                if not event.message.strip():
                    raise ValueError("请输入问题或修改要求。")
                self._edit(state, event.block_id, event.text, event.facts, "manual")
                state.conversations.setdefault(event.block_id, []).append(
                    {"role": "user", "text": event.message, "version": state.version + 1}
                )
            elif event.action == "publish":
                if (
                    len(event.edits) > len(self.blocks)
                    or not event.facts_by_block.keys() <= event.edits.keys()
                ):
                    raise ValueError("待保存段落与补充事实不匹配。")
                for key, text in event.edits.items():
                    self._edit(
                        state,
                        key,
                        text,
                        event.facts_by_block.get(key, state.supplements.get(key, "")),
                        "manual",
                    )
            else:
                raise ValueError("未知的请求。")
            if sum(len(v) for v in state.supplements.values()) > 20000:
                raise ValueError("补充事实总长度超过 20,000 字符。")
            state.version += 1
            state.requests[event.id] = {"status": "running", "message": "请求处理中。"}
            self._write(state)
            return state, True

    def finish(
        self,
        event,
        snapshot,
        message,
        text=None,
        issues=(),
        checked=False,
        usage=(),
        error=False,
        published_output_dir=None,
    ):
        with self.lock:
            state = self._read()
            if state.version != snapshot.version:
                state.requests[event.id] = {
                    "status": "failed",
                    "message": "请求期间草稿已更新，AI 结果未覆盖新修改。",
                }
                self._write(state)
                raise StaleDraft("请求期间草稿已更新，AI 结果未覆盖新修改，请重新发送。")
            if event.action == "ask":
                if text is not None:
                    self._edit(
                        state, event.block_id, text, state.supplements.get(event.block_id, ""), "ai"
                    )
                state.conversations[event.block_id].append(
                    {"role": "assistant", "text": message, "version": state.version + 1}
                )
                if text is not None:
                    state.checks[event.block_id] = {
                        "status": "ai_checked" if checked else "manual",
                        "issues": list(issues),
                    }
                elif issues:
                    current = state.checks.setdefault(
                        event.block_id, {"status": "unchanged", "issues": []}
                    )
                    current["proposal_issues"] = list(issues)
            state.version += 1
            state.requests[event.id] = {
                "status": "failed" if error else "complete",
                "message": message,
                "usage": list(usage),
            }
            if published_output_dir:
                state.requests[event.id]["published_output_dir"] = published_output_dir
            self._write(state)
            return state

    def ask(self, event: ReviewEvent, client):
        state, fresh = self.begin(event)
        if not fresh:
            return state
        usage_start = len(client.usage)
        key = event.block_id
        base = {
            "today": date.today().isoformat(),
            "resume": model_blocks(self.resume),
            "candidate": model_blocks(self.resume, state.texts),
            "job": self.job.model_dump(),
            "candidate_location": candidate_location(self.resume),
            "user_facts": state.supplements,
        }
        try:
            response = client.generate(
                "refine",
                prompts.REFINE,
                base
                | {
                    "selected_block_id": key,
                    "current_text": state.texts[key],
                    "feedback": event.message,
                    "conversation": state.conversations[key][-16:],
                    "risks": [r.model_dump() for r in self.result.risks if r.block_id == key],
                },
                Refinement,
            )
            if response.block_id != key or not set(response.evidence_ids) <= {
                b.id for b in self.resume.blocks if not b.private
            }:
                raise ContractError("AI 回复引用了错误的段落或证据。")
            self._validate(key, response.text, state.supplements.get(key, ""))
            if response.action == "discuss":
                if response.text != state.texts[key]:
                    raise ContractError("AI 回答问题时试图改动正文，已阻止。")
                return self.finish(event, state, response.message, usage=client.usage[usage_start:])
            if not response.text.strip():
                raise ContractError("整段删除请直接清空编辑框后保存，AI 不自动删除整段。")
            if not response.evidence_ids:
                raise ContractError("AI 修改没有提供原文证据。")
            candidate = state.texts | {key: response.text}
            rules = [
                r
                for r in rule_risks(self.resume, candidate, state.supplements)
                if r.block_id == key
            ]
            violations = [r for r in rules if r.severity == "violation" and r.origin == "rewrite"]
            if violations:
                return self.finish(
                    event,
                    state,
                    "这次建议未应用："
                    + "；".join(r.reason for r in violations)
                    + " 可以继续说明实际情况，或在补充事实中填写依据。",
                    issues=[r.model_dump() for r in violations],
                    usage=client.usage[usage_start:],
                )
            audit = client.generate(
                "audit",
                prompts.AUDIT,
                base
                | {"candidate": model_blocks(self.resume, candidate), "review_block_ids": [key]},
                Audit,
            )
            check_audit(audit, candidate, [key])
            semantic = audit_risks(audit, self.resume, candidate)
            violations = [
                r for r in semantic if r.severity == "violation" and r.origin == "rewrite"
            ]
            if violations:
                return self.finish(
                    event,
                    state,
                    "这次建议未应用：" + "；".join(r.reason for r in violations),
                    issues=[r.model_dump() for r in violations],
                    usage=client.usage[usage_start:],
                )
            return self.finish(
                event,
                state,
                response.message + "\n预览已更新；导出前仍需全文审校。",
                text=response.text,
                issues=[r.model_dump() for r in rules + semantic],
                checked=True,
                usage=client.usage[usage_start:],
            )
        except StaleDraft:
            raise
        except (ModelError, ContractError, ValueError) as exc:
            return self.finish(
                event,
                state,
                f"本次 AI 请求未完成：{exc}。已保留你的草稿和问题，可以重试。",
                usage=client.usage[usage_start:],
                error=True,
            )

    def publish(self, event, client, output_root=None, progress=None):
        state, fresh = self.begin(event)
        if not fresh:
            published = state.requests[event.id].get("published_output_dir")
            return load_result(Path(published)) if published else None
        try:
            edits = {k: v for k, v in state.texts.items() if v != self.result.final_text[k]}
            result = run_pipeline(
                self.source,
                self.job_bytes,
                client,
                output_root or self.root.parent,
                previous=self.result,
                edits=edits,
                supplements=state.supplements,
                target_pages=self.result.layout.get("target_pages", 2),
                progress=progress,
            )
        except (ModelError, ValueError, RuntimeError, OSError) as exc:
            self.finish(event, state, f"全文审校未完成：{exc}。草稿已保存。", error=True)
            raise
        child = ReviewWorkspace(result)
        child_state = child.load()
        child_state.conversations = state.conversations
        child_state.history = state.history + [
            {"origin": "publish", "parent_run_id": self.result.run_id}
        ]
        for key, old in state.texts.items():
            if old != result.final_text[key]:
                child_state.conversations.setdefault(key, []).append(
                    {
                        "role": "assistant",
                        "text": "全文审校后这段被调整或回退，请查看更新后的正文与风险记录。",
                        "version": 0,
                    }
                )
        with child.lock:
            child._write(child_state)
        message = (
            "已完成全文审校并保存新版本。"
            if result.audit_complete
            else "审阅版本已保存，但全文事实审校未完成。请查看错误并重试；不提供无标注版。"
        )
        self.finish(event, state, message, published_output_dir=result.output_dir)
        return result
