"""Native REST APIs. Provider credentials never enter persisted run metadata."""

import json
import time
from dataclasses import dataclass, field
from typing import TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
PROVIDERS = {
    "openai": ("OpenAI", "OPENAI_API_KEY", "OPENAI_MODEL", "https://api.openai.com/v1"),
    "gemini": (
        "Gemini",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "https://generativelanguage.googleapis.com/v1beta",
    ),
    "claude": ("Claude", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "https://api.anthropic.com/v1"),
    "deepseek": ("DeepSeek", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "https://api.deepseek.com"),
}


class ModelError(RuntimeError):
    pass


@dataclass
class ModelConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)
    max_tokens: int = 16000
    timeout: float = 180

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ModelError("请选择 OpenAI、Gemini、Claude 或 DeepSeek。")
        self.model = self.model.strip()
        self.api_key = self.api_key.strip()
        if not self.model or not self.api_key:
            raise ModelError("请填写所选服务的模型 ID 和 API 密钥。")


def json_schema(model):
    schema = model.model_json_schema()

    def simplify(node):
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for v in node.values():
                simplify(v)
        elif isinstance(node, list):
            for v in node:
                simplify(v)

    simplify(schema)
    return schema


class LLMClient:
    def __init__(self, config: ModelConfig, transport=None, sleep=time.sleep):
        self.config = config
        self.usage = []
        self.transport = transport
        self.sleep = sleep

    def _request(self, system, payload, schema, stage):
        c = self.config
        base = PROVIDERS[c.provider][3]
        user = json.dumps(payload, ensure_ascii=False)
        headers = {"Content-Type": "application/json"}
        if c.provider == "openai":
            url = base + "/responses"
            headers["Authorization"] = f"Bearer {c.api_key}"
            body = {
                "model": c.model,
                "store": False,
                "instructions": system,
                "input": user,
                "max_output_tokens": c.max_tokens,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": stage,
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
        elif c.provider == "gemini":
            url = (
                base + f"/models/{quote(c.model.removeprefix('models/'), safe='')}:generateContent"
            )
            headers["x-goog-api-key"] = c.api_key
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                    "maxOutputTokens": c.max_tokens,
                },
            }
        elif c.provider == "claude":
            url = base + "/messages"
            headers.update({"x-api-key": c.api_key, "anthropic-version": "2023-06-01"})
            body = {
                "model": c.model,
                "system": system,
                "max_tokens": c.max_tokens,
                "messages": [{"role": "user", "content": user}],
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
            }
        else:
            url = base + "/chat/completions"
            headers["Authorization"] = f"Bearer {c.api_key}"
            body = {
                "model": c.model,
                # DeepSeek JSON mode rejects requests unless the prompt explicitly
                # mentions JSON. Keep the schema validation local because this API
                # accepts json_object rather than the stricter schema envelope used
                # by the other providers.
                "messages": [
                    {
                        "role": "system",
                        "content": system
                        + "\nReturn one valid JSON object only; do not include Markdown or commentary.",
                    },
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": c.max_tokens,
                "stream": False,
            }
        started = time.monotonic()
        with httpx.Client(
            timeout=httpx.Timeout(c.timeout, connect=20),
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            for attempt in range(3):
                try:
                    response = client.post(url, headers=headers, json=body)
                except httpx.RequestError as exc:
                    if attempt == 2:
                        raise ModelError("模型请求连接失败或超时；请检查网络后重试。") from exc
                    self.sleep(2**attempt)
                    continue
                if response.status_code in (408, 429, 500, 502, 503, 504, 529) and attempt < 2:
                    self.sleep(2**attempt)
                    continue
                if response.status_code >= 400:
                    # API response bodies can echo prompts or secrets. Never expose them.
                    hints = {
                        401: "API 密钥无效",
                        403: "账户无权访问",
                        404: "模型 ID 或接口不存在",
                        400: "模型不支持当前结构化输出配置或输入超限",
                        429: "额度不足或限流",
                    }
                    raise ModelError(
                        f"{PROVIDERS[c.provider][0]} HTTP {response.status_code}："
                        + hints.get(response.status_code, "服务请求失败，请稍后重试")
                    )
                try:
                    raw = response.json()
                except ValueError as exc:
                    raise ModelError("模型服务返回了非 JSON 响应。") from exc
                if not isinstance(raw, dict):
                    raise ModelError("模型服务响应结构异常。")
                usage = (
                    raw.get("usageMetadata", {}) if c.provider == "gemini" else raw.get("usage", {})
                )
                self.usage.append(
                    {
                        "stage": stage,
                        "seconds": round(time.monotonic() - started, 2),
                        "attempts": attempt + 1,
                        "tokens": usage,
                    }
                )
                return self._text(raw)
        raise ModelError("模型请求未完成。")

    def _text(self, raw):
        try:
            if self.config.provider == "openai":
                if raw.get("status") != "completed":
                    raise ModelError("OpenAI 输出未完成，可能达到输出上限。")
                contents = [
                    part
                    for item in raw.get("output", [])
                    if item.get("type") == "message"
                    for part in item.get("content", [])
                ]
                if any(x.get("type") == "refusal" for x in contents):
                    raise ModelError("OpenAI 拒绝了本次请求。")
                text = "".join(x["text"] for x in contents if x.get("type") == "output_text")
            elif self.config.provider == "gemini":
                candidates = raw.get("candidates", [])
                if not candidates or candidates[0].get("finishReason") != "STOP":
                    raise ModelError("Gemini 输出被截断、拦截或未完成。")
                text = "".join(
                    p.get("text", "")
                    for p in candidates[0]["content"]["parts"]
                    if not p.get("thought")
                )
            elif self.config.provider == "claude":
                if raw.get("stop_reason") != "end_turn":
                    raise ModelError("Claude 输出被截断、拒绝或未完成。")
                text = "".join(p["text"] for p in raw.get("content", []) if p.get("type") == "text")
            else:
                choices = raw.get("choices", [])
                if not choices or choices[0].get("finish_reason") != "stop":
                    raise ModelError("DeepSeek 输出被截断、拒绝或未完成。")
                text = choices[0].get("message", {}).get("content", "")
            if not text.strip():
                raise ModelError("模型没有返回可用文本。")
            return text
        except (KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ModelError("模型服务响应结构异常。") from exc

    def generate(self, stage: str, system: str, payload: dict, output_type: type[T]) -> T:
        schema = json_schema(output_type)
        for attempt in range(2):
            text = self._request(system, payload, schema, stage)
            try:
                return output_type.model_validate_json(text)
            except ValidationError as exc:
                if attempt:
                    raise ModelError(f"{stage} 阶段未返回符合约定的数据。") from exc
                system += "\nPrevious response failed schema validation. Return only valid JSON exactly matching the schema."
        raise ModelError("结构化生成失败。")
