from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Block(StrictModel):
    id: str
    text: str
    location: str
    editable: bool
    private: bool


class Resume(StrictModel):
    sha256: str
    blocks: list[Block]


class Requirement(StrictModel):
    id: str
    text: str
    path: str
    category: str


class JobSpec(StrictModel):
    title: str
    company: str
    summary: str
    requirements: list[Requirement]
    context: dict
    warnings: list[str]


class Mapping(StrictModel):
    requirement_id: str
    status: Literal["direct", "transferable", "missing", "conflict"]
    evidence_ids: list[str]
    reason: str


class Analysis(StrictModel):
    positioning: str
    priorities: list[str]
    mappings: list[Mapping]
    questions: list[str]


class Change(StrictModel):
    block_id: str
    before: str
    after: str
    evidence_ids: list[str]
    requirement_ids: list[str]
    reason: str


class Draft(StrictModel):
    changes: list[Change]


class Verdict(StrictModel):
    block_id: str
    severity: Literal["clear", "suspected", "violation"]
    origin: Literal["rewrite", "source"]
    quote: str
    reason: str
    suggestion: str


class Audit(StrictModel):
    verdicts: list[Verdict]


class Risk(StrictModel):
    id: str = ""
    block_id: str
    severity: Literal["suspected", "violation"]
    origin: Literal["rewrite", "source", "system"]
    quote: str
    source_text: str
    reason: str
    suggestion: str
    status: Literal["pending", "reverted", "resolved", "deleted"] = "pending"


class RunResult(StrictModel):
    run_id: str
    output_dir: str
    analysis: Analysis
    changes: list[Change]
    risks: list[Risk]
    final_text: dict[str, str]
    audit_complete: bool
    audit_error: str
    layout: dict
    clean_available: bool
    provider: str
    model: str
    usage: list[dict] = Field(default_factory=list)
