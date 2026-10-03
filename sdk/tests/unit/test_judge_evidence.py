"""A judge verdict is auditable from what is stored (SINEF-149)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from m3._judge_evidence import MAX_CLAIMS, MAX_FIELD_BYTES
from m3.evaluations import EvaluationRunner
from m3.judges import LLMJudge
from m3.storage.ephemeral import InMemoryExecutionStore
from m3.storage.sqlite import SQLiteExecutionStore
from m3.trace.redaction import REDACTED, RedactionConfig
from m3.types import (
    EvaluationDecision,
    EvaluationRecord,
    EvaluationStatus,
    ExecutionId,
    ExecutionState,
    JudgeEvidence,
)

SECRET = "sk-judge-evidence-secret-9f3a"


def _store(tmp_path) -> SQLiteExecutionStore:
    store = SQLiteExecutionStore(tmp_path / "evidence.sqlite")
    store.create(
        ExecutionState(
            execution_id=ExecutionId("exec-1"), created_at=datetime.now(timezone.utc)
        ),
        run_id="run-1",
    )
    return store


def _stub_provider(monkeypatch, score: float) -> None:
    monkeypatch.setenv("M3_JUDGE_API_KEY", "judge-key-not-stored")
    monkeypatch.setattr(
        "m3.judges._openai_sync",
        lambda *_: (
            {"score": score, "rationale": "misses the refund window", "abstain": False},
            {"total_tokens": 3},
            "judge",
        ),
    )


def test_failing_llm_judge_persists_every_field_needed_to_audit_the_verdict(
    tmp_path, monkeypatch
) -> None:
    _stub_provider(monkeypatch, 0.35)
    store = _store(tmp_path)
    judge = LLMJudge(
        model="judge",
        threshold=0.8,
        rubric="Every claim must be supported.",
        rubric_id="desk.claims",
        rubric_version="1",
    )
    runner = EvaluationRunner(durable_store=store)
    runner.register("answer.v1", judge)
    result = runner.evaluate(
        {"input": "Refund policy?", "expected": "30 days", "actual": "7 days"},
        "answer.v1",
        execution_id="exec-1",
    )
    assert result.status is EvaluationStatus.FAILED and result.score == 0.35

    record = store.evaluations("exec-1")[0]
    evidence = record.judge_evidence
    assert evidence is not None
    assert evidence.schema_version == "m3.judge_evidence.v1"
    assert (evidence.input, evidence.reference, evidence.candidate) == (
        "Refund policy?",
        "30 days",
        "7 days",
    )
    assert evidence.rubric == "Every claim must be supported."
    assert evidence.threshold == 0.8
    assert evidence.config_digest == judge.config_digest
    assert evidence.rubric_digest is not None and len(evidence.rubric_digest) == 64
    assert evidence.truncated == ()
    # Digest semantics are unchanged: the digest still identifies the subject.
    assert record.subject_digest is not None and len(record.subject_digest) == 64
    # The same bundle reaches the upload payload (the execution report).
    report = store.get_report("exec-1")
    assert report is not None
    uploaded = report.model_dump(mode="json")["evaluations"][0]["judge_evidence"]
    assert uploaded["threshold"] == 0.8 and uploaded["candidate"] == "7 days"


def test_judge_without_rubric_records_none_not_an_empty_string(
    tmp_path, monkeypatch
) -> None:
    _stub_provider(monkeypatch, 0.9)
    runner = EvaluationRunner(durable_store=_store(tmp_path))
    runner.register("answer.v1", LLMJudge(model="judge"))
    result = runner.evaluate(
        {"input": "q", "expected": "a", "actual": "a"},
        "answer.v1",
        execution_id="exec-1",
    )
    assert result.judge_evidence is not None
    assert result.judge_evidence.rubric is None
    assert result.judge_evidence.rubric_digest is None


def test_provider_error_still_records_what_was_asked(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("M3_JUDGE_API_KEY", raising=False)
    runner = EvaluationRunner(durable_store=_store(tmp_path))
    runner.register("answer.v1", LLMJudge(model="judge"))
    result = runner.evaluate(
        {"input": "q", "expected": "a", "actual": "b"},
        "answer.v1",
        execution_id="exec-1",
    )
    assert result.status is EvaluationStatus.ERROR
    assert result.judge_evidence is not None
    assert result.judge_evidence.candidate == "b"


def test_secrets_in_judge_inputs_and_rubric_are_redacted_before_persistence(
    tmp_path, monkeypatch
) -> None:
    _stub_provider(monkeypatch, 0.2)
    database = tmp_path / "evidence.sqlite"
    store = _store(tmp_path)
    runner = EvaluationRunner(
        durable_store=store,
        redaction_config=RedactionConfig(secrets=frozenset({SECRET})),
    )
    runner.register(
        "answer.v1",
        LLMJudge(model="judge", rubric=f"Reject answers that leak {SECRET}."),
    )
    result = runner.evaluate(
        {
            "input": f"Use key {SECRET} to look up the order",
            "expected": "The order shipped",
            "actual": f"I used {SECRET} and the order shipped",
        },
        "answer.v1",
        execution_id="exec-1",
    )
    evidence = store.evaluations("exec-1")[0].judge_evidence
    assert evidence is not None
    for text in (evidence.input, evidence.candidate, evidence.rubric):
        assert text is not None and SECRET not in text and REDACTED in text
    assert evidence.reference == "The order shipped"
    assert result.judge_evidence is not None and SECRET not in str(
        result.judge_evidence.model_dump()
    )
    store.close()
    assert SECRET.encode() not in database.read_bytes()


def test_custom_judge_supplies_the_same_bundle_and_secrets_are_redacted(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    runner = EvaluationRunner(
        durable_store=store,
        redaction_config=RedactionConfig(secrets=frozenset({SECRET})),
    )
    runner.register(
        "claims.v1",
        lambda context: EvaluationDecision(
            status=EvaluationStatus.FAILED,
            score=0.35,
            rationale="two of five claims unsupported",
            judge_evidence=JudgeEvidence(
                input="Summarise ticket 41",
                claims=("status is open", f"owner is {SECRET}"),
                candidate="The ticket is closed",
                rubric="All claims must hold.",
                threshold=0.8,
            ),
        ),
    )
    runner.evaluate({"answer": "x"}, "claims.v1", execution_id="exec-1")
    evidence = store.evaluations("exec-1")[0].judge_evidence
    assert evidence is not None
    assert evidence.claims == ("status is open", f"owner is {REDACTED}")
    assert evidence.reference is None
    assert evidence.rubric_digest is not None
    assert evidence.threshold == 0.8


@pytest.mark.parametrize("sensitive_field", ["input", "candidate"])
def test_a_field_that_is_wholly_a_secret_value_reads_as_redacted(
    sensitive_field,
) -> None:
    runner = EvaluationRunner(
        redaction_config=RedactionConfig(secrets=frozenset({SECRET}))
    )
    runner.register(
        "j.v1",
        lambda _context: EvaluationDecision(
            status=EvaluationStatus.PASSED,
            judge_evidence=JudgeEvidence(**{sensitive_field: SECRET}),
        ),
    )
    result = runner.evaluate({}, "j.v1")
    assert result.judge_evidence is not None
    assert getattr(result.judge_evidence, sensitive_field) == REDACTED


def test_oversized_fields_are_bounded_and_named_in_truncated() -> None:
    runner = EvaluationRunner(redaction_config=RedactionConfig())
    runner.register(
        "big.v1",
        lambda _context: EvaluationDecision(
            status=EvaluationStatus.FAILED,
            judge_evidence=JudgeEvidence(
                input="short",
                candidate="é" * MAX_FIELD_BYTES,
                claims=tuple(f"claim {i}" for i in range(MAX_CLAIMS + 5)),
            ),
        ),
    )
    evidence = runner.evaluate({}, "big.v1").judge_evidence
    assert evidence is not None
    assert evidence.input == "short"
    assert evidence.candidate is not None
    assert len(evidence.candidate.encode()) <= MAX_FIELD_BYTES
    assert evidence.claims is not None and len(evidence.claims) == MAX_CLAIMS
    assert evidence.truncated == ("candidate", "claims")


def test_async_custom_judge_evidence_is_kept() -> None:
    import asyncio

    runner = EvaluationRunner(redaction_config=RedactionConfig())

    async def judge(_context):
        return EvaluationDecision(
            status=EvaluationStatus.PASSED,
            score=0.9,
            judge_evidence=JudgeEvidence(candidate="ok", threshold=0.5),
        )

    runner.register("async.v1", judge)
    result = asyncio.run(runner.evaluate_async({}, "async.v1"))
    assert result.judge_evidence is not None
    assert result.judge_evidence.threshold == 0.5


def test_ephemeral_store_keeps_the_bundle() -> None:
    store = InMemoryExecutionStore()
    store.create(
        ExecutionState(
            execution_id=ExecutionId("exec-1"), created_at=datetime.now(timezone.utc)
        )
    )
    runner = EvaluationRunner(durable_store=store, redaction_config=RedactionConfig())
    runner.register(
        "j.v1",
        lambda _context: EvaluationDecision(
            status=EvaluationStatus.FAILED,
            judge_evidence=JudgeEvidence(candidate="wrong", threshold=0.8),
        ),
    )
    runner.evaluate({}, "j.v1", execution_id="exec-1")
    evidence = store.evaluations("exec-1")[0].judge_evidence
    assert evidence is not None and evidence.candidate == "wrong"


def test_records_written_before_judge_evidence_still_load(tmp_path) -> None:
    store = _store(tmp_path)
    old = {
        "evaluation_id": "evaluation-old",
        "name": "answer.v1",
        "status": "failed",
        "required": True,
        "score": 0.35,
        "rationale": "old record",
        "metrics": {},
        "provenance": {"kind": "llm_judge", "model": "judge"},
        "details": {},
        "context": {"execution_id": "exec-1"},
        "subject_kind": "json",
        "subject_digest": "a" * 64,
        "run_id": "run-1",
        "created_at": "2026-10-02T21:39:38.128887Z",
    }
    store.close()
    with sqlite3.connect(tmp_path / "evidence.sqlite") as connection:
        connection.execute(
            "INSERT INTO v2_evaluations(id,execution_id,turn_id,result_json,created_at,"
            "evaluator_name,status,score,run_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "evaluation-old",
                "exec-1",
                None,
                json.dumps(old),
                old["created_at"],
                "answer.v1",
                "failed",
                0.35,
                "run-1",
            ),
        )
    reopened = SQLiteExecutionStore(tmp_path / "evidence.sqlite")
    records = reopened.evaluations("exec-1")
    assert [record.judge_evidence for record in records] == [None]
    assert reopened.all()[0].judge_evidence is None
    bare = {k: v for k, v in old.items() if k != "context"}
    assert (
        EvaluationRecord.model_validate(
            {**bare, "execution_id": "exec-1"}
        ).judge_evidence
        is None
    )


@pytest.mark.parametrize("threshold", [-0.1, 1.5, float("nan"), True])
def test_threshold_must_be_a_finite_fraction(threshold) -> None:
    with pytest.raises(ValueError, match="threshold"):
        JudgeEvidence(threshold=threshold)
