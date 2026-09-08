from datetime import date

import pytest

from resume_studio.parsing import parse_job, parse_resume
from resume_studio.schemas import Audit, Change, Draft, Verdict
from resume_studio.validation import ContractError, check_audit, check_draft, rule_risks


def test_rate_to_velocity_same_number_is_blocked(resume_bytes):
    resume = parse_resume(resume_bytes)
    final = {b.id: b.text for b in resume.blocks}
    b = next(b for b in resume.blocks if "approval rates" in b.text)
    final[b.id] = b.text.replace("approval rates", "approval velocity")
    assert any(r.severity == "violation" and "含义" in r.reason for r in rule_risks(resume, final))


@pytest.mark.parametrize("skill", ["SQL", "DCF", "NPV", "WorkBoard", "Tableau", "Power BI"])
def test_unsupported_skills_blocked(resume_bytes, skill):
    resume = parse_resume(resume_bytes)
    final = {b.id: b.text for b in resume.blocks}
    b = next(b for b in resume.blocks if "Evaluated 200+" in b.text)
    final[b.id] += f" Used {skill}."
    assert any(r.severity == "violation" and skill in r.reason for r in rule_risks(resume, final))


def test_number_bounds_preserved(resume_bytes):
    resume = parse_resume(resume_bytes)
    final = {b.id: b.text.replace("200+", "200") for b in resume.blocks}
    assert any(r.severity == "violation" for r in rule_risks(resume, final))


def test_source_expired_certificate(resume_bytes):
    resume = parse_resume(resume_bytes)
    b = resume.blocks[-1]
    b.text = "Google Cloud Generative AI Leader Certification - in progress; expected Aug 2026"
    final = {b.id: b.text for b in resume.blocks}
    risks = rule_risks(resume, final, today=date(2026, 9, 7))
    assert len(risks) == 1 and risks[0].origin == "source" and risks[0].status == "pending"
    final[b.id] = "Google Cloud Generative AI Leader Certification - Aug 2026"
    assert any(r.severity == "violation" for r in rule_risks(resume, final))


def test_harmless_paraphrase_not_flagged(resume_bytes):
    resume = parse_resume(resume_bytes)
    final = {b.id: b.text.replace("Evaluated 200+", "Assessed 200+") for b in resume.blocks}
    assert not rule_risks(resume, final)


def test_draft_cannot_edit_identity(resume_bytes, job_bytes):
    resume, job = parse_resume(resume_bytes), parse_job(job_bytes)
    b = resume.blocks[0]
    draft = Draft(
        changes=[
            Change(
                block_id=b.id,
                before=b.text,
                after="NEW NAME",
                evidence_ids=[b.id],
                requirement_ids=[],
                reason="bad",
            )
        ]
    )
    with pytest.raises(ContractError):
        check_draft(draft, resume, job)


def test_audit_cannot_silently_skip_or_misanchor():
    v = Verdict(
        block_id="b001",
        severity="suspected",
        origin="rewrite",
        quote="wrong text",
        reason="Not grounded",
        suggestion="Revert",
    )
    with pytest.raises(ContractError):
        check_audit(Audit(verdicts=[v]), {"b001": "real text"}, ["b001"])
    with pytest.raises(ContractError):
        check_audit(Audit(verdicts=[]), {"b001": "real text"}, ["b001"])
