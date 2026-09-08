import json

import httpx
import pytest

from resume_studio.llm import LLMClient, ModelConfig, ModelError
from resume_studio.schemas import Draft


def envelope(provider, text):
    if provider == "openai":
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 5},
        }
    if provider == "gemini":
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [{"text": "private thinking", "thought": True}, {"text": text}]
                    },
                }
            ],
            "usageMetadata": {"promptTokenCount": 5},
        }
    if provider == "deepseek":
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": text}}],
            "usage": {"prompt_tokens": 5},
        }
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 5},
    }


@pytest.mark.parametrize("provider", ["openai", "gemini", "claude", "deepseek"])
def test_native_request_and_response(provider):
    def handler(request):
        data = json.loads(request.content)
        assert "TOP-SECRET" not in str(request.url) and "TOP-SECRET" not in request.content.decode()
        if provider == "openai":
            assert request.url.path == "/v1/responses"
            assert request.headers["authorization"] == "Bearer TOP-SECRET"
            assert data["text"]["format"]["strict"] is True and data["store"] is False
        elif provider == "gemini":
            assert request.url.path.endswith(":generateContent")
            assert request.headers["x-goog-api-key"] == "TOP-SECRET"
            assert data["generationConfig"]["responseMimeType"] == "application/json"
            assert data["generationConfig"]["responseJsonSchema"]["additionalProperties"] is False
        else:
            if provider == "claude":
                assert request.url.path == "/v1/messages"
                assert request.headers["x-api-key"] == "TOP-SECRET"
                assert data["output_config"]["format"]["type"] == "json_schema"
            else:
                assert request.url.path == "/chat/completions"
                assert request.headers["authorization"] == "Bearer TOP-SECRET"
                assert data["response_format"] == {"type": "json_object"}
                assert data["thinking"] == {"type": "disabled"}
                assert "JSON" in data["messages"][0]["content"]
                assert '"properties"' in data["messages"][0]["content"]
        return httpx.Response(200, json=envelope(provider, '{"changes":[]}'))

    client = LLMClient(
        ModelConfig(provider, "test-model", "TOP-SECRET"), httpx.MockTransport(handler)
    )
    assert client.generate("tailoring", "instructions", {"text": "input"}, Draft).changes == []
    assert client.usage[0]["stage"] == "tailoring"
    assert "TOP-SECRET" not in repr(client.config)


@pytest.mark.parametrize("provider", ["openai", "gemini", "claude", "deepseek"])
def test_truncation_is_not_success(provider):
    data = envelope(provider, '{"changes":[]}')
    if provider == "openai":
        data["status"] = "incomplete"
    elif provider == "gemini":
        data["candidates"][0]["finishReason"] = "MAX_TOKENS"
    elif provider == "claude":
        data["stop_reason"] = "max_tokens"
    else:
        data["choices"][0]["finish_reason"] = "length"
    client = LLMClient(
        ModelConfig(provider, "test", "secret"),
        httpx.MockTransport(lambda r: httpx.Response(200, json=data)),
    )
    with pytest.raises(ModelError):
        client.generate("tailoring", "system", {}, Draft)


def test_rate_limit_retry_is_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "secret echoed"})

    client = LLMClient(
        ModelConfig("openai", "test", "secret"), httpx.MockTransport(handler), sleep=lambda _: None
    )
    with pytest.raises(ModelError) as exc:
        client.generate("tailoring", "system", {}, Draft)
    assert len(calls) == 3 and "secret" not in str(exc.value)


def test_invalid_json_schema_response_retries_once():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=envelope("openai", '{"not_changes": []}'))

    client = LLMClient(ModelConfig("openai", "test", "secret"), httpx.MockTransport(handler))
    with pytest.raises(ModelError):
        client.generate("tailoring", "system", {}, Draft)
    assert len(calls) == 2


@pytest.mark.parametrize("provider", ["openai", "gemini", "claude", "deepseek"])
def test_full_file_pipeline_through_native_adapter(provider, resume_bytes, job_bytes, tmp_path):
    from resume_studio.pipeline import run_pipeline

    stages = []

    def handler(request):
        body = json.loads(request.content)
        if provider == "openai":
            payload = json.loads(body["input"])
        elif provider == "gemini":
            payload = json.loads(body["contents"][0]["parts"][0]["text"])
        elif provider == "claude":
            payload = json.loads(body["messages"][0]["content"])
        else:
            payload = json.loads(body["messages"][1]["content"])
        if "review_block_ids" in payload:
            stages.append("audit")
            response = {
                "verdicts": [
                    {
                        "block_id": b,
                        "severity": "clear",
                        "origin": "source",
                        "quote": "",
                        "reason": "",
                        "suggestion": "",
                    }
                    for b in payload["review_block_ids"]
                ]
            }
        elif "analysis" in payload:
            stages.append("tailoring")
            block = next(b for b in payload["resume"] if "Evaluated 200+" in b["text"])
            response = {
                "changes": [
                    {
                        "block_id": block["id"],
                        "before": block["text"],
                        "after": block["text"].replace("Evaluated", "Assessed"),
                        "evidence_ids": [block["id"]],
                        "requirement_ids": ["j000"],
                        "reason": "突出已有评估经验",
                    }
                ]
            }
        else:
            stages.append("analysis")
            response = {
                "positioning": "测试定位",
                "priorities": [],
                "questions": [],
                "mappings": [
                    {
                        "requirement_id": r["id"],
                        "status": "missing",
                        "evidence_ids": [],
                        "reason": "测试映射",
                    }
                    for r in payload["job"]["requirements"]
                ],
            }
        return httpx.Response(200, json=envelope(provider, json.dumps(response)))

    client = LLMClient(ModelConfig(provider, "TEST-DOUBLE", "secret"), httpx.MockTransport(handler))
    result = run_pipeline(resume_bytes, job_bytes, client, tmp_path)
    assert result.clean_available and stages == ["analysis", "tailoring", "audit"]
    assert any("Assessed 200+" in text for text in result.final_text.values())
