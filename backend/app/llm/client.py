"""Model client for the Meta Model API (Muse Spark / Muse Wonder).

Calls the Responses API (POST {MODEL_BASE_URL}/responses) as in Meta's docs: `instructions` for the system
prompt, `input` for the conversation, `reasoning.effort`, `max_output_tokens`. Requests are built as chat
messages internally (so cache keys don't depend on the wire format) and converted in _post(). Set
MODEL_API=chat to use the OpenAI-compatible POST {MODEL_BASE_URL}/chat/completions instead.
"""
import json
import re
from typing import Callable, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .. import config
from . import cache, usage

T = TypeVar("T", bound=BaseModel)


class ModelNotConfigured(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


def _user_content(user: str, image: str | None):
    """Plain text, or chat-completions content parts when an image (data URL) is attached."""
    if not image:
        return user
    return [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": image}}]


def _responses_payload(messages: list[dict], model: str, temperature: float, json_mode: bool,
                       max_tokens: int | None, effort: str | None) -> dict:
    """Chat messages -> a Responses API request: the system prompt becomes `instructions`, images `input_image`."""
    def part(p: dict) -> dict:
        if p.get("type") == "image_url":
            return {"type": "input_image", "image_url": p["image_url"]["url"]}
        return {"type": "input_text", "text": p.get("text", "")}

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [{"role": m["role"], "content": m["content"] if isinstance(m["content"], str) else [part(p) for p in m["content"]]}
             for m in messages if m["role"] != "system"]
    payload = {"model": model, "input": turns, "temperature": temperature}
    if system:
        payload["instructions"] = system
    if max_tokens:
        payload["max_output_tokens"] = max_tokens
    if effort:
        payload["reasoning"] = {"effort": effort}
    if json_mode and config.MODEL_JSON_MODE:
        payload["text"] = {"format": {"type": "json_object"}}
    return payload


def _responses_reply(body: dict) -> tuple[str, int, int]:
    """Text, input tokens and output tokens from a Responses API reply. A reply cut off by the cap is an error."""
    if body.get("status") == "incomplete":
        why = (body.get("incomplete_details") or {}).get("reason", "incomplete")
        raise ModelError(f"Model reply was cut off ({why})")
    parts = [c.get("text", "") for item in body.get("output") or [] if isinstance(item, dict) and item.get("type") == "message"
             for c in item.get("content") or [] if isinstance(c, dict) and c.get("type") == "output_text"]
    text = "".join(parts) or body.get("output_text") or ""
    u = body.get("usage") or {}
    return text, u.get("input_tokens", 0), u.get("output_tokens", 0)


def _chat_payload(messages: list[dict], model: str, temperature: float, json_mode: bool,
                  max_tokens: int | None, effort: str | None) -> dict:
    payload = {"model": model, "temperature": temperature, "messages": messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if effort:
        payload["reasoning_effort"] = effort
    if json_mode and config.MODEL_JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _chat_reply(body: dict) -> tuple[str, int, int]:
    choice = body["choices"][0]
    if choice.get("finish_reason") == "length":  # hit the output cap
        raise ModelError("Model reply was cut off (max_output_tokens)")
    u = body.get("usage") or {}
    return choice["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def _post(messages: list[dict], temperature: float = 0.0, model: str | None = None,
          json_mode: bool = False, max_tokens: int | None = None, effort: str | None = None,
          cache_reply: bool = True) -> str:
    if not config.model_configured():
        raise ModelNotConfigured(
            "No model configured. Set MODEL_BASE_URL, MODEL_API_KEY and MODEL_NAME in backend/.env")
    model = model or config.MODEL_NAME
    effort = config.REASONING_EFFORT if effort is None else effort
    k = cache.key(model, messages, max_tokens, effort)
    if (hit := cache.get(k)) is not None:
        usage.record(cache_hit=True)
        return hit
    chat = config.MODEL_API == "chat"
    build, read = (_chat_payload, _chat_reply) if chat else (_responses_payload, _responses_reply)
    payload = build(messages, model, temperature, json_mode, max_tokens, effort)
    headers = {"Authorization": f"Bearer {config.MODEL_API_KEY}"}
    try:
        r = httpx.post(f"{config.MODEL_BASE_URL}/{'chat/completions' if chat else 'responses'}", json=payload,
                       headers=headers, timeout=config.MODEL_TIMEOUT_S)
        r.raise_for_status()
        # A reply cut off by the output cap raises here: never pass off or cache a partial reply.
        content, tokens_in, tokens_out = read(r.json())
        if not content:
            raise ValueError("empty reply (output cap reached?)")
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise ModelError(f"Model call failed: {e}") from e
    usage.record(tokens_in, tokens_out, call=True)
    if not json_mode and cache_reply:
        cache.put(k, content)  # JSON replies are cached by chat_model once they validate
    return content


def _messages(system: str, user: str, image: str | None = None) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": _user_content(user, image)}]


def _parse(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def chat_text(system: str, user: str, max_tokens: int | None = None,
              validate: Callable[[str], None] | None = None) -> str:
    """Plain-text reply. With `validate` (raises ModelError on a bad reply), only a reply that passes is cached."""
    messages = _messages(system, user)
    if validate is None:
        return _post(messages, temperature=0.2, max_tokens=max_tokens).strip()
    text = _post(messages, temperature=0.2, max_tokens=max_tokens, cache_reply=False).strip()
    validate(text)
    cache.put(cache.key(config.MODEL_NAME, messages, max_tokens, config.REASONING_EFFORT), text)
    return text


def chat_model(system: str, user: str, schema: type[T], image: str | None = None,
               model: str | None = None, retries: int = 1, max_tokens: int | None = None,
               effort: str | None = None) -> T:
    """Ask for JSON and validate it. A reply that fails validation is sent back once for correction."""
    messages = _messages(system, user, image)
    # Cache the validated reply under the original request, so a repeat skips any correction round.
    first_key = cache.key(model or config.MODEL_NAME, messages, max_tokens,
                          config.REASONING_EFFORT if effort is None else effort)
    for attempt in range(retries + 1):
        raw = _post(messages, temperature=0.0, model=model, json_mode=True, max_tokens=max_tokens,
                    effort=effort)
        try:
            out = schema.model_validate(_parse(raw))
            cache.put(first_key, raw)
            return out
        except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
            if attempt == retries:
                raise ModelError(f"Model did not return valid JSON: {e}") from e
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"Your reply was not valid: {e}. Return only the corrected JSON object."},
            ]
    raise AssertionError("unreachable")


def chat_json(system: str, user: str, image: str | None = None, model: str | None = None) -> dict:
    raw = _post(_messages(system, user, image), temperature=0.0, model=model, json_mode=True)
    try:
        return _parse(raw)
    except ValueError as e:
        raise ModelError(f"Model did not return valid JSON: {e}") from e
