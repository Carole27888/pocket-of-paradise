"""Thin LLM client with a provider choice: Claude (Anthropic) and/or OpenAI. One function does each HTTP call so tests can replace it.
Keys come from Integration Keys (DB) or env. Whichever is preferred goes first; the other is the backup when it is missing or fails.
Callers always get an Anthropic-shaped reply (content blocks of type text / tool_use) and fall back to plain text on AIUnavailable."""
import json
import urllib.error
import urllib.request

from django.conf import settings

from apps.system.services import get_credential

URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


class AIUnavailable(Exception):
    """No key, no quota, or the provider is down. Callers fall back to a polite human hand-over."""


def has_claude() -> bool:
    return bool(get_credential("ai", "api_key"))


def has_openai() -> bool:
    return bool(get_credential("openai", "api_key"))


def configured() -> bool:
    return has_claude() or has_openai()


def order() -> list:
    """Providers to try, preferred first, only those with a key saved."""
    pref = (get_credential("ai", "prefer") or "claude").strip().lower()
    names = ["openai", "claude"] if pref.startswith("open") else ["claude", "openai"]
    ready = [n for n in names if (has_claude() if n == "claude" else has_openai())]
    try:
        from .models import BotSettings

        if not BotSettings.get().ai_fallback:
            return ready[:1]
    except Exception:
        pass
    return ready


def active_provider() -> str:
    o = order()
    return o[0] if o else ""


def _http(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={**headers, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AIUnavailable(f"AI provider error {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise AIUnavailable("AI provider unreachable") from e


def _post(payload: dict) -> dict:
    key = get_credential("ai", "api_key")
    if not key:
        raise AIUnavailable("No AI key saved. Add one under ICT → AI.")
    return _http(URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}, payload)


# ---- OpenAI adapter: Anthropic-shaped in, Anthropic-shaped out
def _to_openai_messages(system: str, messages: list) -> list:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        texts = [b.get("text", "") for b in c if b.get("type") == "text"]
        calls = [b for b in c if b.get("type") == "tool_use"]
        results = [b for b in c if b.get("type") == "tool_result"]
        if calls:
            out.append({"role": "assistant", "content": "".join(texts) or None,
                        "tool_calls": [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))}} for b in calls]})
        for b in results:
            content = b.get("content")
            out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": content if isinstance(content, str) else json.dumps(content)})
        if texts and not calls:
            out.append({"role": m["role"], "content": "".join(texts)})
    return out


def _post_openai(system: str, messages: list, tools, max_tokens: int) -> dict:
    key = get_credential("openai", "api_key")
    if not key:
        raise AIUnavailable("No OpenAI key saved.")
    payload = {"model": get_credential("openai", "model") or OPENAI_DEFAULT_MODEL, "max_tokens": max_tokens, "messages": _to_openai_messages(system, messages)}
    if tools:
        payload["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema", {"type": "object", "properties": {}})}} for t in tools]
    data = _http(OPENAI_URL, {"authorization": f"Bearer {key}"}, payload)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise AIUnavailable("AI provider returned nothing") from e
    blocks = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls") or []:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = {}
        blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": args})
    return {"content": blocks, "stop_reason": "tool_use" if msg.get("tool_calls") else "end_turn", "provider": "openai", "usage": data.get("usage") or {}}


def complete(system: str, messages: list, *, tools: list | None = None, max_tokens=600, feature: str = "general") -> dict:
    names = order()
    if not names:
        raise AIUnavailable("No AI key saved. Add one under ICT → AI.")
    last = None
    for name in names:
        model = (get_credential("openai", "model") or OPENAI_DEFAULT_MODEL) if name == "openai" else (get_credential("ai", "model") or settings.AI_MODEL)
        try:
            if name == "openai":
                resp = _post_openai(system, messages, tools, max_tokens)
            else:
                payload = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
                if tools:
                    payload["tools"] = tools
                resp = _post(payload)
                resp.setdefault("provider", "claude")
            _record(feature, name, model, resp.get("usage") or {})
            return resp
        except AIUnavailable as e:
            _record(feature, name, model, {}, error=str(e))
            last = e
    raise last


def _record(feature, provider, model, usage, error=""):
    """Log the call with an estimated cost (prices per million tokens are set in ICT → AI). Never breaks the caller."""
    try:
        from .models import AICall, MessagingSettings

        cfg = MessagingSettings.get()
        tin = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        tout = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        pin, pout = (cfg.openai_input_price, cfg.openai_output_price) if provider == "openai" else (cfg.claude_input_price, cfg.claude_output_price)
        from decimal import Decimal

        cost = (Decimal(tin) * pin + Decimal(tout) * pout) / Decimal(1_000_000)
        AICall.objects.create(feature=feature[:30], provider=provider, model=(model or "")[:60], input_tokens=tin, output_tokens=tout, cost_usd=cost,
                              ok=not error, error=error[:240])
    except Exception:  # pragma: no cover - logging must never stop an answer
        pass


def text_of(resp: dict) -> str:
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()
