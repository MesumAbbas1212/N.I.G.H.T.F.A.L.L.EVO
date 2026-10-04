"""Jeff model routing.

`jeff <https://github.com/logan-markewich/jeff>`_ is a self-hosted drop-in
replacement for TypeSafe's *jev System One API*: given a piece of text (the
"state") it answers small classification questions about it - ``choice`` (pick
one option), ``score`` (rate on ordered levels) and ``noul`` (probability of
yes). NIGHTFALL Evo uses exactly that to decide *which model should answer a
request*.

The request text is sent to Jeff together with routing questions such as
"which capability does this need?" and "how complex is this?". The answers are
turned into a routing decision over the providers registered in
:mod:`core.provider_registry`. When no Jeff server is configured (or it is
unreachable) a local heuristic router is used instead, so routing always works.

Wire format (compatible with the official ``typesafe-sdk``)::

    POST {base_url}/v1/systemone
    {"state": "...", "model": "jev-latest", "questions": {
        "capability": {"type": "choice", "instructions": "...",
                       "criteria": {"coding": null, "chat": null}},
        "complexity": {"type": "score", "instructions": "...",
                       "criteria": ["low", "medium", "high"]},
        "needs_vision": {"type": "noul", "instructions": "..."}
    }}
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core import provider_registry as registry

DEFAULT_JEFF_BASE_URL = "http://localhost:8000"
DEFAULT_JEFF_MODEL = "jev-latest"

#: Capabilities Jeff may classify a request into. ``caps`` tags on a provider
#: (see core/provider_registry.py) are matched against these.
CAPABILITY_CAPS = {
    "coding": "coding",
    "reasoning": "reasoning",
    "conversation": "chat",
    "creative_writing": "chat",
    "analysis": "reasoning",
    "long_document": "long_context",
    "vision": "vision",
    "fast_answer": "fast",
}

ROUTING_QUESTIONS: dict[str, dict] = {
    "capability": {
        "type": "choice",
        "instructions": (
            "Which single capability does this request need most?"
        ),
        "criteria": {
            "coding": "writing, debugging or explaining code",
            "reasoning": "maths, logic, planning or deep analysis",
            "conversation": "everyday chat, small talk or a quick factual answer",
            "creative_writing": "essays, stories, scripts or long-form writing",
            "long_document": "reading or writing a very long document",
            "vision": "understanding an image or screenshot",
            "fast_answer": "a very short answer where speed matters most",
        },
    },
    "complexity": {
        "type": "score",
        "instructions": "How complex is this request?",
        "criteria": ["low", "medium", "high"],
    },
    "needs_long_context": {
        "type": "noul",
        "instructions": "Does this request need a very large context window?",
    },
    "needs_vision": {
        "type": "noul",
        "instructions": "Does this request require understanding an image?",
    },
    "is_time_sensitive": {
        "type": "noul",
        "instructions": "Does this request need the fastest possible reply?",
    },
}

_HEURISTIC_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("coding", ("code", "python", "javascript", "bug", "debug", "function", "compile",
                "error", "script", "api", "sql", "regex", "class ", "refactor", "stack trace")),
    ("reasoning", ("prove", "theorem", "derive", "calculate", "solve", "logic", "why does",
                   "analyze", "analyse", "compare", "strategy", "plan", "math", "equation")),
    ("long_document", ("summarize this", "summarise this", "read this document", "entire file",
                       "whole book", "long document", "transcript", "pdf", "pages of")),
    ("creative_writing", ("write a story", "write an essay", "poem", "script", "blog post",
                          "report", "article", "letter", "draft")),
    ("vision", ("screenshot", "image", "picture", "photo", "this diagram", "look at")),
    ("fast_answer", ("quick", "briefly", "in one line", "tldr", "short answer", "asap")),
]

_COMPLEXITY_HINTS = {
    "high": ("explain in detail", "step by step", "in depth", "comprehensive", "detailed",
             "architecture", "design a", "implement", "optimize", "optimise", "research"),
    "low": ("hi", "hello", "thanks", "what time", "who is", "define", "yes or no"),
}


@dataclass
class RouteDecision:
    """Which model should answer, and why."""

    provider: dict
    capability: str = "conversation"
    complexity: str = "medium"
    reason: str = ""
    source: str = "heuristic"          # "jeff" | "heuristic" | "fallback"
    confidence: float = 0.0
    scores: dict = field(default_factory=dict)

    @property
    def provider_id(self) -> str:
        return str(self.provider.get("id") or "")

    @property
    def provider_name(self) -> str:
        return str(self.provider.get("name") or self.provider_id)

    @property
    def model(self) -> str:
        return str(self.provider.get("model") or "")

    def describe(self) -> str:
        return (
            f"Jeff → {self.provider_name} [{self.model}] "
            f"(capability={self.capability}, complexity={self.complexity}, "
            f"router={self.source}, confidence={self.confidence:.2f})"
        )


class JeffClient:
    """Minimal client for a jeff / jev System One server."""

    def __init__(
        self,
        base_url: str = DEFAULT_JEFF_BASE_URL,
        api_key: str = "",
        model: str = DEFAULT_JEFF_MODEL,
        timeout: float = 6.0,
    ) -> None:
        self.base_url = (base_url or DEFAULT_JEFF_BASE_URL).rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_JEFF_MODEL).strip()
        self.timeout = timeout
        self._healthy: Optional[bool] = None
        self._checked_at = 0.0

    # ---------------------------------------------------------------- plumbing
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def health(self, force: bool = False) -> bool:
        """Cached ``GET /healthz`` probe (re-checked at most every 30s)."""
        now = time.time()
        if not force and self._healthy is not None and (now - self._checked_at) < 30:
            return self._healthy
        self._checked_at = now
        try:
            import requests

            resp = requests.get(
                f"{self.base_url}/healthz", headers=self._headers(), timeout=self.timeout
            )
            self._healthy = resp.status_code == 200
        except Exception:
            self._healthy = False
        return bool(self._healthy)

    def system_one(self, state: str, questions: dict) -> Optional[dict]:
        """POST /v1/systemone and return the decoded result."""
        payload = {"state": state, "model": self.model, "questions": questions}
        try:
            import requests

            resp = requests.post(
                f"{self.base_url}/v1/systemone",
                headers=self._headers(),
                json=payload,
                timeout=max(self.timeout, 15.0),
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    # ----------------------------------------------------------------- routing
    def classify(self, text: str) -> Optional[dict]:
        result = self.system_one(text, ROUTING_QUESTIONS)
        if not result:
            return None
        choices = result.get("choices") or {}
        scores = result.get("scores") or {}
        nouls = result.get("nouls") or {}

        capability = ""
        entry = choices.get("capability") or {}
        if isinstance(entry, dict):
            capability = str(entry.get("choice") or "").strip().lower()
        if capability not in CAPABILITY_CAPS:
            capability = ""

        complexity = ""
        entry = scores.get("complexity") or {}
        if isinstance(entry, dict):
            complexity = str(entry.get("score") or "").strip().lower()
        if complexity not in ("low", "medium", "high"):
            complexity = ""

        flags = {}
        for name, entry in (nouls or {}).items():
            if isinstance(entry, dict):
                try:
                    flags[str(name)] = float(entry.get("noul") or 0.0)
                except (TypeError, ValueError):
                    flags[str(name)] = 0.0
        if not capability and not flags:
            return None
        return {"capability": capability, "complexity": complexity, "flags": flags}


def _heuristic(text: str) -> dict:
    low = (text or "").lower()
    hits: dict[str, int] = {}
    for capability, keywords in _HEURISTIC_HINTS:
        hits[capability] = sum(1 for keyword in keywords if keyword in low)
    capability = max(hits, key=lambda key: hits[key]) if any(hits.values()) else "conversation"
    if hits.get(capability, 0) == 0:
        capability = "conversation"

    complexity = "medium"
    if any(token in low for token in _COMPLEXITY_HINTS["high"]) or len(text or "") > 600:
        complexity = "high"
    elif any(token in low for token in _COMPLEXITY_HINTS["low"]) or len(text or "") < 40:
        complexity = "low"

    # Mirror the noul questions Jeff would answer, so scoring is identical.
    flags = {
        "needs_vision": 1.0 if capability == "vision" else 0.0,
        "needs_long_context": 1.0 if (capability == "long_document" or len(text or "") > 3000) else 0.0,
        "is_time_sensitive": 1.0 if (capability == "fast_answer" or complexity == "low") else 0.0,
    }
    return {"capability": capability, "complexity": complexity, "flags": flags}


def _score_provider(provider: dict, capability: str, complexity: str, flags: dict) -> tuple[float, str]:
    caps = {str(c).lower() for c in (provider.get("caps") or [])}
    needed = CAPABILITY_CAPS.get(capability, "chat")
    score = 0.0
    reasons: list[str] = []

    if needed in caps:
        score += 3.0
        reasons.append(f"supports {needed}")
    else:
        score -= 2.5
        reasons.append(f"missing {needed}")

    if "chat" in caps:
        score += 0.5

    if complexity == "high":
        if "reasoning" in caps or "long_context" in caps:
            score += 1.5
            reasons.append("strong for complex work")
    elif complexity == "low":
        if "fast" in caps or "cheap" in caps:
            score += 1.5
            reasons.append("fast/cheap for a simple ask")
        if "reasoning" in caps:
            score -= 0.5

    for flag in ("needs_vision",):
        if flags.get(flag, 0.0) >= 0.5:
            if "vision" in caps:
                score += 2.5
                reasons.append("vision capable")
            else:
                score -= 4.0
                reasons.append("no vision support")
    if flags.get("needs_long_context", 0.0) >= 0.5:
        if "long_context" in caps:
            score += 1.5
            reasons.append("large context")
        else:
            score -= 1.0
    if flags.get("is_time_sensitive", 0.0) >= 0.6:
        if "fast" in caps:
            score += 1.0
            reasons.append("low latency")
    if provider.get("kind") == "local":
        score += 0.15  # small tiebreaker: free and offline-friendly
    # Lower priority number = preferred; convert to a small bonus.
    try:
        score += max(0.0, (60 - int(provider.get("priority") or 50)) / 100.0)
    except (TypeError, ValueError):
        pass
    return score, ", ".join(reasons)


def route(
    text: str,
    client: Optional[JeffClient] = None,
    providers: Optional[list] = None,
    allow_heuristic: bool = True,
) -> RouteDecision:
    """Decide which configured provider should answer ``text``."""
    available = providers if providers is not None else registry.configured_providers()
    # Never route to a provider that cannot actually be called.
    available = [
        p for p in available
        if p.get("kind") == "local" or str(p.get("api_key") or "").strip()
    ]
    if not available:
        raise RuntimeError("No AI provider is configured. Add an API key in Settings.")

    classification: Optional[dict] = None
    source = "heuristic"
    if client is not None:
        classification = client.classify(text)
        if classification:
            source = "jeff"

    if classification is None:
        if not allow_heuristic:
            raise RuntimeError("Jeff routing is unavailable and the heuristic router is disabled.")
        classification = _heuristic(text)

    capability = classification.get("capability") or "conversation"
    complexity = classification.get("complexity") or "medium"
    flags = classification.get("flags") or {}

    best: Optional[tuple[float, dict, str]] = None
    ranked: dict[str, float] = {}
    for provider in available:
        score, reason = _score_provider(provider, capability, complexity, flags)
        ranked[provider.get("id") or provider.get("name") or "?"] = round(score, 2)
        if best is None or score > best[0]:
            best = (score, provider, reason)

    assert best is not None
    score, provider, reason = best
    confidence = 0.0
    if source == "jeff":
        confidence = min(1.0, 0.45 + max(0.0, score) / 12.0)
    else:
        confidence = min(0.6, 0.2 + max(0.0, score) / 12.0)

    return RouteDecision(
        provider=provider,
        capability=capability,
        complexity=complexity,
        reason=reason or "default provider",
        source=source,
        confidence=confidence,
        scores=ranked,
    )


def client_from_settings(settings: Optional[dict] = None) -> Optional[JeffClient]:
    """Build a JeffClient from app settings (None when disabled/unconfigured)."""
    if settings is None:
        try:
            from memory import config_manager

            settings = config_manager.load_settings()
        except Exception:
            settings = {}
    settings = settings or {}
    if not settings.get("jeff_routing_enabled", False):
        return None
    base_url = str(settings.get("jeff_base_url") or DEFAULT_JEFF_BASE_URL).strip()
    return JeffClient(
        base_url=base_url,
        api_key=str(settings.get("jeff_api_key") or "").strip(),
        model=str(settings.get("jeff_model") or DEFAULT_JEFF_MODEL).strip(),
    )


def route_and_chat(
    text: str,
    system: str = "You are NIGHTFALL Evo, a helpful assistant.",
    client: Optional[JeffClient] = None,
    history: Optional[list] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    providers: Optional[list] = None,
) -> tuple[str, RouteDecision]:
    """Route ``text`` and answer it with the selected provider."""
    decision = route(text, client=client, providers=providers)
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": text})
    reply = registry.chat(
        decision.provider,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return reply, decision


def settings_summary() -> dict:
    """Everything the Settings UI needs to render the Jeff panel."""
    try:
        from memory import config_manager

        settings = config_manager.load_settings()
    except Exception:
        settings = {}
    return {
        "enabled": bool(settings.get("jeff_routing_enabled", False)),
        "base_url": str(settings.get("jeff_base_url") or DEFAULT_JEFF_BASE_URL),
        "api_key": str(settings.get("jeff_api_key") or ""),
        "model": str(settings.get("jeff_model") or DEFAULT_JEFF_MODEL),
        "questions": ROUTING_QUESTIONS,
        "providers": registry.describe_providers(),
    }


def _strip_json(text: str) -> Any:
    clean = (text or "").strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        if clean.startswith("json"):
            clean = clean[4:]
    return json.loads(clean.strip())
