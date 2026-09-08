import uuid

import pytest

from resume_studio.llm import ModelError
from resume_studio.pipeline import run_pipeline
from resume_studio.preview import preview_payload
from resume_studio.schemas import Audit, Refinement, ReviewEvent, Verdict
from resume_studio.workspace import ReviewWorkspace, StaleDraft, load_result


@pytest.fixture
def workspace(tmp_path, resume_bytes, job_bytes, fake_model):
    return ReviewWorkspace(run_pipeline(resume_bytes, job_bytes, fake_model(), tmp_path))


def event(ws, action="save", **kwargs):
    key = next(b.id for b in ws.resume.blocks if b.editable and len(b.text) > 110)
    return ReviewEvent(
        id=uuid.uuid4().hex,
        version=ws.load().version,
        action=action,
        **({"block_id": key, "text": ws.load().texts[key]} | kwargs),
    )


def refine_model(fake_model, text, *, action="revise", audit_fn=None, fail_audit=False):
    model = fake_model(audit_fn=audit_fn, fail_audit=fail_audit)
    generate = model.generate

    def refine(stage, system, payload, output_type):
        if stage == "refine":
            model.payloads.append((stage, payload))
            model.usage.append({"stage": stage})
            return Refinement(
                block_id=payload["selected_block_id"],
                action=action,
                text=text,
                message="已根据反馈压缩表述。",
                evidence_ids=[payload["selected_block_id"]],
            )
        return generate(stage, system, payload, output_type)

    model.generate = refine
    return model


def test_manual_persist_undo_restore_and_exports_immutable(workspace):
    ws = workspace
    previous = (ws.root / "resume_review.docx").read_bytes()
    e = event(ws, text="A concise summary of existing strategy experience.")
    state = ws.local(e)
    assert ws.dirty(state)
    assert state.checks[e.block_id]["status"] == "manual"
    assert ReviewWorkspace(load_result(ws.root)).load() == state
    assert (ws.root / "resume_review.docx").read_bytes() == previous
    assert ws.local(e) == state  # component replay is idempotent
    restored = ws.local(event(ws, "undo", block_id=e.block_id))
    assert restored.texts == ws.result.final_text
    ws.local(event(ws, text="Another concise paragraph."))
    restored = ws.local(event(ws, "restore", block_id=e.block_id))
    assert restored.texts[e.block_id] == ws.blocks[e.block_id].text


def test_locked_input_and_stale_tabs_are_rejected(workspace):
    ws = workspace
    stale = event(ws)
    ws.local(event(ws, text="Saved in another window."))
    with pytest.raises(StaleDraft):
        ws.local(stale)
    for key, text in [
        ("b000", "Changed identity"),
        ("invalid", "x"),
        (stale.block_id, "two\nparagraphs"),
    ]:
        with pytest.raises(ValueError):
            ws.local(event(ws, block_id=key, text=text))


def test_ai_updates_only_selected_paragraph_and_replay_costs_nothing(workspace, fake_model):
    ws = workspace
    text = "Strategy leader translating market research into executive recommendations across scientific and business teams."
    model = refine_model(fake_model, text)
    e = event(ws, "ask", message="简洁一点")
    state = ws.ask(e, model)
    assert state.texts == ws.result.final_text | {e.block_id: text}
    assert state.checks[e.block_id]["status"] == "ai_checked"
    assert len(state.conversations[e.block_id]) == 2
    assert [s for s, _ in model.payloads] == ["refine", "audit"]
    ws.ask(e, model)
    assert len(model.payloads) == 2
    assert "person@example.com" not in str(model.payloads)
    assert model.payloads[1][1]["review_block_ids"] == [e.block_id]


@pytest.mark.parametrize("extra", [" SQL", " increasing revenue by 99%"])
def test_ai_unsupported_skills_and_numbers_never_applied(workspace, fake_model, extra):
    ws = workspace
    e = event(ws, "ask", message="加入这些能力")
    state = ws.ask(e, refine_model(fake_model, e.text + extra))
    assert state.texts[e.block_id] == e.text
    assert state.checks[e.block_id]["proposal_issues"]
    assert state.checks[e.block_id]["proposal_issues"]
    assert not state.supplements  # conversation requests aren't factual evidence


@pytest.mark.parametrize("severity,applied", [("suspected", True), ("violation", False)])
def test_semantic_risks_block_or_mark(workspace, fake_model, severity, applied):
    ws = workspace
    e = event(ws, "ask", message="突出领导力")
    proposed = e.text.replace("Strategy leader", "Global strategy leader")

    def audit(payload):
        return Audit(
            verdicts=[
                Verdict(
                    block_id=e.block_id,
                    severity=severity,
                    origin="rewrite",
                    quote="Global",
                    reason="原文未说明全球范围",
                    suggestion="核实范围",
                )
            ]
        )

    state = ws.ask(e, refine_model(fake_model, proposed, audit_fn=audit))
    assert state.texts[e.block_id] == (proposed if applied else e.text)
    field = "issues" if applied else "proposal_issues"
    assert state.checks[e.block_id][field][0]["severity"] == severity
    payload = preview_payload(ws, state)
    assert payload["blocks"][e.block_id]["check"][field]


def test_api_failure_preserves_manual_draft_and_question(workspace, fake_model):
    ws = workspace
    e = event(
        ws,
        "ask",
        message="帮我检查",
        text="Strategy leader with existing market research experience.",
    )
    state = ws.ask(
        e, refine_model(fake_model, e.text + " With executive recommendations.", fail_audit=True)
    )
    assert state.texts[e.block_id] == e.text
    assert state.requests[e.id]["status"] == "failed"
    assert state.conversations[e.block_id][0]["text"] == e.message
    assert state.checks[e.block_id]["status"] == "manual"


def test_supplements_explicit_and_discussion_does_not_modify(workspace, fake_model):
    ws = workspace
    e = event(
        ws, "ask", message="能不能加 SQL？", facts="I used SQL to query portfolio project records."
    )
    model = refine_model(fake_model, e.text, action="discuss")
    state = ws.ask(e, model)
    assert state.texts == ws.result.final_text
    assert model.payloads[0][1]["user_facts"] == {e.block_id: e.facts}
    assert ws.dirty(state)


def test_publish_uses_all_drafts_keeps_parent_and_can_clear_facts(workspace, fake_model):
    ws = workspace
    e = event(
        ws, text="Strategy leader translating market research into executive recommendations."
    )
    ws.local(e)
    published = ws.publish(event(ws, "publish"), fake_model())
    assert published.run_id != ws.result.run_id
    assert published.final_text[e.block_id] == e.text
    assert load_result(ws.root).final_text == ws.result.final_text
    assert not ReviewWorkspace(published).dirty(ReviewWorkspace(published).load())
    ws2 = ReviewWorkspace(published)
    ws2.local(event(ws2, facts="Real supporting details."))
    next_result = ws2.publish(event(ws2, "publish"), fake_model())
    ws3 = ReviewWorkspace(next_result)
    ws3.local(event(ws3, facts=""))
    assert ws3.dirty(ws3.load())
    last = ws3.publish(event(ws3, "publish"), fake_model())
    assert not any(ReviewWorkspace(last).base_facts.values())


def test_publish_rollbacks_are_visible_in_new_preview(workspace, fake_model):
    ws = workspace
    e = event(ws, "publish")
    e.edits = {e.block_id: e.text + " Expert SQL."}
    published = ws.publish(e, fake_model())
    child = ReviewWorkspace(published)
    assert published.final_text[e.block_id] == ws.blocks[e.block_id].text
    assert any("回退" in m["text"] for m in child.load().conversations[e.block_id])
    assert (
        preview_payload(child, child.load())["blocks"][e.block_id]["text"]
        == published.final_text[e.block_id]
    )


def test_concurrent_ai_never_overwrites_newer_draft(workspace, fake_model):
    ws = workspace
    e = event(ws, "ask", message="压缩")
    model = refine_model(fake_model, e.text)
    generate = model.generate

    def concurrent(stage, *args):
        if stage == "refine":
            ws.local(event(ws, text="Newer manual work from another window."))
        return generate(stage, *args)

    model.generate = concurrent
    with pytest.raises(StaleDraft):
        ws.ask(e, model)
    assert ws.load().texts[e.block_id] == "Newer manual work from another window."
    assert ws.load().requests[e.id]["status"] == "failed"


def test_publish_failure_preserves_saved_inputs(workspace, fake_model):
    ws = workspace
    model = fake_model()
    model.generate = lambda *a: (_ for _ in ()).throw(ModelError("服务不可用"))
    e = event(ws, "publish")
    e.edits = {e.block_id: "My saved manual draft."}
    with pytest.raises(ModelError):
        ws.publish(e, model)
    assert ws.load().texts[e.block_id] == "My saved manual draft."
    assert ws.load().requests[e.id]["status"] == "failed"


def test_preview_keeps_document_order_and_table_cells(workspace):
    data = preview_payload(workspace, workspace.load())
    table = next(n for n in data["nodes"] if n["type"] == "table")
    assert len(table["rows"][0]) == 2
    keys = []

    def walk(nodes):
        for n in nodes:
            if n["type"] == "paragraph":
                keys.append(n["id"])
            elif n["type"] == "table":
                for row in n["rows"]:
                    for cell in row:
                        walk(cell["nodes"])
            elif n["type"] == "aside":
                walk(n["nodes"])

    walk(data["nodes"])
    assert keys == list(workspace.blocks)
    assert data["blocks"]["b000"]["editable"] is False


def test_later_discussion_and_blocked_proposal_keep_existing_suspicions(workspace, fake_model):
    ws = workspace
    e = event(ws, "ask", message="修改")
    proposed = e.text.replace("Strategy leader", "Global strategy leader")

    def suspicious(payload):
        return Audit(
            verdicts=[
                Verdict(
                    block_id=e.block_id,
                    severity="suspected",
                    origin="rewrite",
                    quote="Global",
                    reason="范围待核实",
                    suggestion="核实",
                )
            ]
        )

    ws.ask(e, refine_model(fake_model, proposed, audit_fn=suspicious))
    ws.ask(
        event(ws, "ask", message="解释原因"), refine_model(fake_model, proposed, action="discuss")
    )
    state = ws.ask(
        event(ws, "ask", message="再加 SQL"), refine_model(fake_model, proposed + " SQL")
    )
    assert state.checks[e.block_id]["status"] == "ai_checked"
    assert state.checks[e.block_id]["issues"][0]["quote"] == "Global"
    assert state.checks[e.block_id]["proposal_issues"]


def test_entire_ai_deletion_is_not_silently_applied(workspace, fake_model):
    e = event(workspace, "ask", message="更简洁")
    state = workspace.ask(e, refine_model(fake_model, ""))
    assert state.texts[e.block_id] == e.text
    assert state.requests[e.id]["status"] == "failed"


def test_incomplete_publish_never_claims_audit_success_and_replay_reopens_result(
    workspace, fake_model
):
    ws = workspace
    e = event(ws, "publish")
    model = fake_model(fail_audit=True)
    result = ws.publish(e, model)
    assert not result.audit_complete and not result.clean_available
    assert "未完成" in ws.load().requests[e.id]["message"]
    calls = len(model.payloads)
    assert ws.publish(e, model).run_id == result.run_id
    assert len(model.payloads) == calls
