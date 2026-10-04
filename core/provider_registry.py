"""Multi-provider API keys and chat adapters.

NIGHTFALL Evo is not limited to a fixed set of vendors: any provider that
speaks one of the common wire formats can be added from Settings, including
small or brand-new services that expose an OpenAI-compatible endpoint.

Providers are stored in ``config/api_keys.json`` under ``custom_providers`` and
merged with the built-ins (Gemini / OpenRouter / Anthropic / local Ollama).
Each entry carries its own key, base URL, default model and the capabilities it
should be used for, so the Jeff router can pick the right one per request.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from core.user_paths import get_user_data_dir

#: Wire formats understood by :func:`chat` / :func:`vision`.
KINDS = ("openai", "anthropic", "gemini", "local")

#: Capability tags used by the Jeff router.
CAPABILITIES = (
    "chat", "coding", "reasoning", "vision", "long_context", "fast", "cheap",
)

PRESETS: list[dict] = [
    # name, kind, base_url, default model, notes
    {"name": "Groq", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
     "model": "llama-3.3-70b-versatile", "caps": ["chat", "coding", "fast"]},
    {"name": "Together AI", "kind": "openai", "base_url": "https://api.together.xyz/v1",
     "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "caps": ["chat", "coding"]},
    {"name": "Mistral", "kind": "openai", "base_url": "https://api.mistral.ai/v1",
     "model": "mistral-large-latest", "caps": ["chat", "coding", "reasoning"]},
    {"name": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-chat", "caps": ["chat", "coding", "reasoning", "cheap"]},
    {"name": "Cerebras", "kind": "openai", "base_url": "https://api.cerebras.ai/v1",
     "model": "llama-3.3-70b", "caps": ["chat", "fast"]},
    {"name": "xAI (Grok)", "kind": "openai", "base_url": "https://api.x.ai/v1",
     "model": "grok-3-mini", "caps": ["chat", "reasoning"]},
    {"name": "Fireworks AI", "kind": "openai", "base_url": "https://api.fireworks.ai/inference/v1",
     "model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "caps": ["chat", "fast"]},
    {"name": "Perplexity", "kind": "openai", "base_url": "https://api.perplexity.ai",
     "model": "sonar", "caps": ["chat", "reasoning"]},
    {"name": "SambaNova", "kind": "openai", "base_url": "https://api.sambanova.ai/v1",
     "model": "Meta-Llama-3.3-70B-Instruct", "caps": ["chat", "fast"]},
    {"name": "Nebius AI Studio", "kind": "openai", "base_url": "https://api.studio.nebius.ai/v1",
     "model": "meta-llama/Llama-3.3-70B-Instruct-fast", "caps": ["chat", "coding"]},
    {"name": "Hyperbolic", "kind": "openai", "base_url": "https://api.hyperbolic.xyz/v1",
     "model": "meta-llama/Llama-3.3-70B-Instruct", "caps": ["chat", "cheap"]},
    {"name": "Novita AI", "kind": "openai", "base_url": "https://api.novita.ai/v3/openai",
     "model": "meta-llama/llama-3.3-70b-instruct", "caps": ["chat", "cheap"]},
    {"name": "Chutes", "kind": "openai", "base_url": "https://llm.chutes.ai/v1",
     "model": "deepseek-ai/DeepSeek-V3", "caps": ["chat", "coding", "cheap"]},
    {"name": "GitHub Models", "kind": "openai", "base_url": "https://models.inference.ai.azure.com",
     "model": "gpt-4o-mini", "caps": ["chat", "coding", "fast"]},
    {"name": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1",
     "model": "auto", "caps": ["chat", "coding", "reasoning", "vision", "long_context"]},
    {"name": "Anthropic (Claude)", "kind": "anthropic", "base_url": "https://api.anthropic.com",
     "model": "claude-sonnet-4-5", "caps": ["chat", "coding", "reasoning", "long_context"]},
    {"name": "Google AI Studio (Gemini)", "kind": "gemini",
     "base_url": "https://generativelanguage.googleapis.com",
     "model": "gemini-2.5-flash", "caps": ["chat", "coding", "reasoning", "vision", "fast"]},
    {"name": "Cohere", "kind": "openai", "base_url": "https://api.cohere.ai/compatibility/v1",
     "model": "command-r-plus", "caps": ["chat", "reasoning"]},
    {"name": "AI21 Studio", "kind": "openai", "base_url": "https://api.ai21.com/studio/v1",
     "model": "jamba-1.5-large", "caps": ["chat", "long_context"]},
    {"name": "Ollama (local)", "kind": "local", "base_url": "http://localhost:11434/v1",
     "model": "llama3.2", "caps": ["chat", "coding", "cheap"]},
    {"name": "LM Studio (local)", "kind": "local", "base_url": "http://localhost:1234/v1",
     "model": "local-model", "caps": ["chat", "cheap"]},
    {"name": "vLLM / any OpenAI-compatible server", "kind": "openai",
     "base_url": "http://localhost:8000/v1", "model": "my-model", "caps": ["chat"]},
    {"name": "Other OpenAI-compatible API", "kind": "openai",
     "base_url": "", "model": "", "caps": ["chat"]},
]

BUILTIN_PROVIDERS: list[dict] = [
    {"id": "gemini", "name": "Google Gemini", "kind": "gemini",
     "base_url": "https://generativelanguage.googleapis.com",
     "key_field": "gemini_api_key", "model": "gemini-2.5-flash",
     "caps": ["chat", "coding", "reasoning", "vision", "fast"], "builtin": True},
    {"id": "openrouter", "name": "OpenRouter", "kind": "openai",
     "base_url": "https://openrouter.ai/api/v1",
     "key_field": "openrouter_api_key", "model": "auto",
     "caps": ["chat", "coding", "reasoning", "vision", "long_context"], "builtin": True},
    {"id": "anthropic", "name": "Anthropic", "kind": "anthropic",
     "base_url": "https://api.anthropic.com",
     "key_field": "anthropic_api_key", "model": "claude-sonnet-4-5",
     "caps": ["chat", "coding", "reasoning", "long_context"], "builtin": True},
    {"id": "local", "name": "Local (Ollama / LM Studio)", "kind": "local",
     "base_url": "http://localhost:11434/v1", "key_field": "",
     "model": "llama3.2", "caps": ["chat", "coding", "cheap"], "builtin": True},
]


# --------------------------------------------------------------------------- io
def api_keys_path() -> Path:
    path = get_user_data_dir() / "config" / "api_keys.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def _repo_keys_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "api_keys.json"


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_keys() -> dict:
    """Keys live in the user data dir; the repo copy is the fallback."""
    data = _read_json(api_keys_path())
    if not data:
        data = _read_json(_repo_keys_path())
    return data


def save_keys(data: dict) -> None:
    try:
        api_keys_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_api_key(field: str, default: str = "") -> str:
    if not field:
        return default
    return str(load_keys().get(field, "") or "").strip()


def set_api_key(field: str, value: str) -> None:
    if not field:
        return
    data = load_keys()
    data[field] = (value or "").strip()
    save_keys(data)


# ------------------------------------------------------------------ providers
def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug or "provider"


def _normalize(entry: dict) -> dict:
    entry = dict(entry or {})
    name = str(entry.get("name") or entry.get("id") or "Provider").strip()
    entry["name"] = name
    entry.setdefault("id", _slug(name))
    entry["id"] = _slug(str(entry.get("id") or name))
    entry["kind"] = str(entry.get("kind") or "openai").strip().lower()
    if entry["kind"] not in KINDS:
        entry["kind"] = "openai"
    entry["base_url"] = str(entry.get("base_url") or "").strip().rstrip("/")
    entry["api_key"] = str(entry.get("api_key") or "").strip()
    entry["model"] = str(entry.get("model") or "").strip()
    caps = entry.get("caps") or []
    if isinstance(caps, str):
        caps = [c.strip() for c in caps.split(",") if c.strip()]
    entry["caps"] = [str(c).strip().lower() for c in caps if str(c).strip()]
    try:
        entry["priority"] = int(entry.get("priority") or 50)
    except Exception:
        entry["priority"] = 50
    entry["enabled"] = bool(entry.get("enabled", True))
    models = entry.get("models") or []
    if isinstance(models, str):
        models = [m.strip() for m in models.split(",") if m.strip()]
    entry["models"] = [str(m) for m in models if str(m).strip()]
    entry["custom"] = True
    return entry


def list_custom_providers() -> list[dict]:
    raw = load_keys().get("custom_providers") or []
    if not isinstance(raw, list):
        return []
    return [_normalize(item) for item in raw if isinstance(item, dict)]


def save_custom_provider(entry: dict) -> dict:
    """Insert or update a custom provider (matched on its id)."""
    normalized = _normalize(entry)
    data = load_keys()
    providers = [p for p in (data.get("custom_providers") or []) if isinstance(p, dict)]
    replaced = False
    for index, existing in enumerate(providers):
        if _slug(str(existing.get("id") or existing.get("name") or "")) == normalized["id"]:
            providers[index] = normalized
            replaced = True
            break
    if not replaced:
        providers.append(normalized)
    data["custom_providers"] = providers
    save_keys(data)
    return normalized


def remove_custom_provider(provider_id: str) -> bool:
    target = _slug(provider_id)
    data = load_keys()
    providers = [p for p in (data.get("custom_providers") or []) if isinstance(p, dict)]
    kept = [p for p in providers if _slug(str(p.get("id") or p.get("name") or "")) != target]
    if len(kept) == len(providers):
        return False
    data["custom_providers"] = kept
    save_keys(data)
    return True


def all_providers(include_disabled: bool = False) -> list[dict]:
    """Built-ins plus custom providers, cheapest/highest priority first."""
    providers: list[dict] = []
    for builtin in BUILTIN_PROVIDERS:
        entry = dict(builtin)
        entry["custom"] = False
        entry["enabled"] = True
        if entry.get("key_field"):
            entry["api_key"] = get_api_key(entry["key_field"])
        else:
            entry["api_key"] = ""
        entry.setdefault("priority", 10 if entry["id"] == "gemini" else 20)
        providers.append(entry)
    providers.extend(list_custom_providers())
    if not include_disabled:
        providers = [p for p in providers if p.get("enabled", True)]
    providers.sort(key=lambda p: (int(p.get("priority") or 50), str(p.get("name") or "")))
    return providers


def get_provider(provider_id: str, include_disabled: bool = True) -> Optional[dict]:
    target = _slug(provider_id)
    for provider in all_providers(include_disabled=True):
        if provider["id"] == target:
            if not include_disabled and not provider.get("enabled", True):
                return None
            return provider
    return None


def describe_configured() -> str:
    """One-line summary of every provider that is usable right now.

    Used by the assistant when the user asks what it has access to, so the
    answer matches the Settings screen instead of a second, empty store.
    """
    ready = configured_providers()
    if not ready:
        return "No AI provider is configured."
    parts = []
    for provider in ready:
        model = provider.get("model") or "default model"
        parts.append(f"{provider.get('name') or provider.get('id')} ({model})")
    return f"{len(ready)} provider(s) available: " + "; ".join(parts)


def _match_preset(name: str, base_url: str = "") -> Optional[dict]:
    target_name = _slug(name)
    target_url = (base_url or "").strip().rstrip("/").lower()
    for preset in PRESETS:
        if target_name and _slug(preset.get("name", "")) == target_name:
            return preset
    if target_url:
        for preset in PRESETS:
            url = str(preset.get("base_url") or "").rstrip("/").lower()
            if url and (url == target_url or url in target_url):
                return preset
    return None


def _match_builtin(name: str, base_url: str = "") -> Optional[dict]:
    target_name = _slug(name)
    target_url = (base_url or "").strip().rstrip("/").lower()
    for builtin in BUILTIN_PROVIDERS:
        if target_name and target_name in (_slug(builtin["id"]), _slug(builtin["name"])):
            return builtin
        url = str(builtin.get("base_url") or "").rstrip("/").lower()
        if target_url and url and url in target_url:
            return builtin
    return None


def upsert_from_credentials(
    name: str,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    kind: str = "",
    caps: Optional[Iterable[str]] = None,
) -> Optional[dict]:
    """Create or update a provider from a name plus a key/URL.

    This is what the assistant uses when it is told "my Groq key is ..." or
    "add provider Agnes AI at https://...". Well-known names reuse the matching
    preset for base URL, model and capabilities; anything else becomes a plain
    OpenAI-compatible provider, so unknown services work too. A well-known
    *built-in* (gemini / openrouter / anthropic) stores its key in the usual
    field instead of creating a duplicate provider.
    """
    name = (name or "").strip()
    if not name and not base_url:
        return None
    api_key = (api_key or "").strip()
    base_url = (base_url or "").strip().rstrip("/")

    builtin = _match_builtin(name, base_url)
    if builtin and builtin.get("key_field"):
        if api_key:
            set_api_key(builtin["key_field"], api_key)
        return get_provider(builtin["id"])

    preset = _match_preset(name, base_url) or {}
    entry = {
        "name": name or preset.get("name") or "Custom provider",
        "kind": kind or preset.get("kind") or "openai",
        "base_url": base_url or preset.get("base_url") or "",
        "api_key": api_key,
        "model": model or preset.get("model") or "",
        "caps": list(caps) if caps else list(preset.get("caps") or ["chat"]),
        "enabled": True,
    }
    existing = None
    for provider in list_custom_providers():
        if provider["id"] == _slug(entry["name"]):
            existing = provider
            break
    if existing:
        entry["api_key"] = api_key or existing.get("api_key", "")
        entry["base_url"] = entry["base_url"] or existing.get("base_url", "")
        entry["model"] = entry["model"] or existing.get("model", "")
        entry["priority"] = existing.get("priority", 50)
    return save_custom_provider(entry)


def sync_legacy_providers(entries: dict) -> int:
    """Import providers stored by the older ``dynamic_providers.json`` store.

    Both stores existed side by side: Settings wrote real providers, while the
    assistant's ``dynamic_api_configuration`` skill kept its own file and
    reported "0 registered dynamic providers" even though providers were set
    up. Everything in the legacy file is folded into the single provider list
    so both surfaces always agree.
    """
    if not isinstance(entries, dict):
        return 0
    known = {p["id"] for p in list_custom_providers()}
    imported = 0
    for name, values in entries.items():
        if not isinstance(values, dict):
            continue
        api_key = str(values.get("key") or values.get("api_key") or "").strip()
        base_url = str(values.get("base_url") or "").strip()
        if not api_key and not base_url:
            continue
        if _slug(name) in known:
            continue
        if upsert_from_credentials(name, api_key=api_key, base_url=base_url):
            imported += 1
            known.add(_slug(name))
    return imported


#: A keyless "local" provider is only usable while its server is actually up.
#: Probing is a cheap socket connect, cached so routing never blocks on it.
_LOCAL_PROBE_TTL_UP = 60.0
_LOCAL_PROBE_TTL_DOWN = 120.0
_local_probe: dict = {"at": 0.0, "up": False, "key": ""}


def _probe_local(base_url: str) -> bool:
    """True when something is listening on the local provider's host:port."""
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url or "http://localhost:11434")
        host = parsed.hostname or "localhost"
        port = parsed.port or (11434 if "11434" in base_url else 80)
    except Exception:
        host, port = "localhost", 11434
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except Exception:
        return False


def local_ai_running(force: bool = False) -> bool:
    """Cached check for a running Ollama / LM Studio / vLLM server."""
    local = next((p for p in BUILTIN_PROVIDERS if p["id"] == "local"), None)
    base_url = str((local or {}).get("base_url") or "http://localhost:11434/v1")
    now = time.time()
    ttl = _LOCAL_PROBE_TTL_UP if _local_probe["up"] else _LOCAL_PROBE_TTL_DOWN
    if (not force and _local_probe["key"] == base_url
            and (now - float(_local_probe["at"])) < ttl):
        return bool(_local_probe["up"])
    up = _probe_local(base_url)
    _local_probe.update({"at": now, "up": up, "key": base_url})
    return up


def configured_providers() -> list[dict]:
    """Providers that can actually be called right now.

    A keyless local provider only counts while its server answers, so routing
    never sends a request to an Ollama that is not running.
    """
    ready = []
    for provider in all_providers():
        if provider.get("kind") == "local":
            if provider.get("custom"):
                # A user-added local provider is an explicit choice: keep it,
                # but note whether it answered so callers can warn.
                ready.append(provider)
            elif local_ai_running():
                ready.append(provider)
        elif provider.get("api_key"):
            ready.append(provider)
    return ready


def provider_label(provider: dict) -> str:
    model = provider.get("model") or "default model"
    return f"{provider.get('name') or provider.get('id')} ({model})"


# -------------------------------------------------------------------- calling
def _headers(provider: dict) -> dict:
    kind = provider.get("kind")
    key = provider.get("api_key") or ""
    if kind == "anthropic":
        return {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _endpoint(provider: dict, path: str) -> str:
    base = str(provider.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError(f"{provider.get('name')}: no base URL configured")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def chat(
    provider: dict,
    messages: list[dict],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 60,
) -> str:
    """Send a chat completion to any configured provider."""
    import requests

    kind = provider.get("kind")
    model = (model or provider.get("model") or "").strip()
    if not model:
        raise RuntimeError(f"{provider.get('name')}: no model configured")
    system = ""
    turns: list[dict] = []
    for message in messages or []:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            system = (system + "\n" + content).strip() if system else content
        else:
            turns.append({"role": role, "content": content})

    if kind == "anthropic":
        payload: dict = {"model": model, "max_tokens": max_tokens, "messages": turns}
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        resp = requests.post(
            _endpoint(provider, "/v1/messages"),
            headers=_headers(provider), json=payload, timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = [p.get("text", "") for p in (data.get("content") or []) if isinstance(p, dict)]
        return "".join(parts).strip()

    if kind == "gemini":
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user",
             "parts": [{"text": m["content"]}]}
            for m in turns
        ]
        payload = {
            "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = _endpoint(provider, f"/v1beta/models/{model}:generateContent")
        resp = requests.post(url, headers=_headers(provider), json=payload, timeout=timeout)
        if resp.status_code >= 400:
            # Some deployments expect the key as a query parameter instead.
            key = provider.get("api_key") or ""
            if key:
                resp = requests.post(
                    f"{url}?key={key}", headers={"Content-Type": "application/json"},
                    json=payload, timeout=timeout,
                )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        for candidate in candidates:
            for part in ((candidate.get("content") or {}).get("parts") or []):
                if part.get("text"):
                    return str(part["text"]).strip()
        return ""

    payload = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else []) + turns,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        _endpoint(provider, "/chat/completions"),
        headers=_headers(provider), json=payload, timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def vision(
    provider: dict,
    prompt: str,
    image_b64: str,
    mime: str = "image/png",
    system: str = "Analyze the image.",
    model: Optional[str] = None,
    max_tokens: int = 1024,
    timeout: int = 90,
) -> str:
    """Image + text request for providers that support vision."""
    import requests

    model = (model or provider.get("model") or "").strip()
    if not model:
        raise RuntimeError(f"{provider.get('name')}: no model configured")
    kind = provider.get("kind")

    if kind == "gemini":
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": image_b64}},
                    {"text": prompt},
                ],
            }],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = _endpoint(provider, f"/v1beta/models/{model}:generateContent")
        resp = requests.post(url, headers=_headers(provider), json=payload, timeout=timeout)
        if resp.status_code >= 400:
            key = provider.get("api_key") or ""
            if key:
                resp = requests.post(
                    f"{url}?key={key}", headers={"Content-Type": "application/json"},
                    json=payload, timeout=timeout,
                )
        resp.raise_for_status()
        data = resp.json()
        for candidate in (data.get("candidates") or []):
            for part in ((candidate.get("content") or {}).get("parts") or []):
                if part.get("text"):
                    return str(part["text"]).strip()
        return ""

    if kind == "anthropic":
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": mime, "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        resp = requests.post(
            _endpoint(provider, "/v1/messages"),
            headers=_headers(provider), json=payload, timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = [p.get("text", "") for p in (data.get("content") or []) if isinstance(p, dict)]
        return "".join(parts).strip()

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ]},
        ],
    }
    resp = requests.post(
        _endpoint(provider, "/chat/completions"),
        headers=_headers(provider), json=payload, timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    return str(((choices[0].get("message") or {}).get("content") or "")).strip() if choices else ""


def list_models(provider: dict, timeout: int = 20) -> list[str]:
    """Ask an OpenAI-compatible endpoint which models it serves."""
    import requests

    if provider.get("kind") not in ("openai", "local"):
        return []
    try:
        resp = requests.get(
            _endpoint(provider, "/models"), headers=_headers(provider), timeout=timeout
        )
        if resp.status_code >= 400:
            return []
        data = resp.json()
        items = data.get("data") or data.get("models") or []
        names = [str(item.get("id") or item.get("name") or "") for item in items if isinstance(item, dict)]
        return [n for n in names if n]
    except Exception:
        return []


def test_provider(provider: dict, timeout: int = 30) -> tuple[bool, str]:
    """Cheap liveness check: one tiny completion."""
    if provider.get("kind") != "local" and not provider.get("api_key"):
        return False, "No API key configured."
    if not provider.get("model"):
        return False, "No model configured."
    try:
        reply = chat(
            provider,
            [{"role": "user", "content": "Reply with the single word: OK"}],
            max_tokens=8,
            temperature=0,
            timeout=timeout,
        )
        if reply:
            return True, f"Connected — model replied: {reply[:60]}"
        return False, "Connected, but the model returned an empty reply."
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def describe_providers(providers: Optional[Iterable[dict]] = None) -> str:
    """Human-readable summary used in prompts and logs."""
    providers = list(providers if providers is not None else configured_providers())
    if not providers:
        return "(no providers configured)"
    lines = []
    for provider in providers:
        caps = ", ".join(provider.get("caps") or []) or "chat"
        lines.append(f"- {provider['id']}: {provider['name']} | model={provider.get('model') or '?'} | caps={caps}")
    return "\n".join(lines)


def cache_age(provider: dict) -> float:
    """Unused placeholder kept for API symmetry with older callers."""
    return time.time()
