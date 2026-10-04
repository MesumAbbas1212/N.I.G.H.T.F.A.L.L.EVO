"""Full-content generation for Word documents.

When the user asks for a document ("write a detailed report on MARIE in a word
document") the assistant must produce the *document*, not a description of it.
This module writes the complete body (title, sections, prose) and hands it to
``actions.docx_tools.word_document`` so a real .docx lands on disk.

Documents are written in two passes -- an outline, then one call per section --
because a single request for a whole multi-page document reliably hits the
model's output-token ceiling.  A truncated response is invalid JSON, which
used to leave the user with a filler template; one small call per section
always fits, so the document that reaches disk is real, subject-specific prose.
"""

from __future__ import annotations

import json as _json
import re
from typing import Callable, Optional

#: Models that reliably return long structured output, best first.
DOCUMENT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-2.0-flash",
]

#: Writing a whole document takes far longer than a short JSON outline.
DOCUMENT_TIMEOUT = 120

#: A single section is short enough that it can never be truncated.
SECTION_TIMEOUT = 90

#: Sections written per request. Batching keeps a document inside a small daily
#: quota (3 calls instead of 8) while staying short enough not to be truncated.
SECTIONS_PER_CALL = 3

#: Section headings used when no model can be reached for an outline.
DEFAULT_SECTION_HEADINGS = [
    "1. Introduction",
    "2. Background and Context",
    "3. Core Concepts",
    "4. Detailed Analysis",
    "5. Worked Examples",
    "6. Applications",
    "7. Conclusion",
]


DOCUMENT_SYSTEM_INSTRUCTION = (
    "You are an expert technical writer producing the FULL BODY of a document. "
    "The user asked for a document, so you must WRITE the document itself. "
    "Never return a summary, an outline, a table of contents, or a description of "
    "what the document will contain - return the actual finished prose.\n"
    "Return ONLY a valid JSON object with this exact schema:\n"
    "{\n"
    '  "title": "Document title",\n'
    '  "subtitle": "Course / subject / audience line",\n'
    '  "sections": [\n'
    "    {\n"
    '      "heading": "Section heading",\n'
    '      "body": "Several substantial paragraphs of real explanatory prose.",\n'
    '      "bullets": ["Key point", "Key point"]\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Requirements:\n"
    "- Write at least 6 sections (introduction, background, core concepts, detailed "
    "analysis, worked examples, applications, conclusion).\n"
    "- Every section body must be several full paragraphs of specific, factual, "
    "teaching-grade content about the requested subject - not filler.\n"
    "- Use concrete terminology, definitions, and worked examples where relevant.\n"
    "- Keep the JSON valid: escape newlines and never leave a trailing comma."
)


OUTLINE_SYSTEM_INSTRUCTION = (
    "You plan documents for an expert technical writer. Given a request, return "
    "ONLY a valid JSON object:\n"
    "{\n"
    '  "title": "A specific, professional document title",\n'
    '  "subtitle": "Course / subject / audience line",\n'
    '  "sections": [{"heading": "Section heading", "brief": "What this section must cover"}]\n'
    "}\n"
    "The briefs are instructions for a later writing pass: make them specific to "
    "the subject (names, definitions, mechanisms, examples to include).\n"
    "Requirements:\n"
    "- 6 to 8 sections, in a logical order, first section an introduction and last "
    "one a conclusion.\n"
    "- Headings must name the actual subject matter, never a placeholder.\n"
    "- Keep the JSON valid: no trailing commas, escape newlines."
)


SECTION_SYSTEM_INSTRUCTION = (
    "You write ONE section of a longer document. Return ONLY a valid JSON object:\n"
    '{"body": "The finished prose for this section", "bullets": ["Key point", ...]}\n'
    "Requirements:\n"
    "- 3 to 5 full paragraphs (roughly 300-450 words) of real, specific, factual "
    "explanatory prose about the requested subject.\n"
    "- Never describe what the section will do; write the section itself.\n"
    "- Define terms, give concrete mechanisms, numbers or examples where relevant.\n"
    "- 'bullets' is optional: 2 to 5 short key points when the section benefits.\n"
    "- Keep the JSON valid: escape newlines, no trailing commas."
)


SECTION_BATCH_SYSTEM_INSTRUCTION = (
    "You write several sections of a longer document. Return ONLY a valid JSON object:\n"
    '{"sections": [{"heading": "The heading you were given", "body": "The finished prose", '
    '"bullets": ["Key point", ...]}]}\n'
    "Requirements:\n"
    "- One entry per requested section, in the order given, keeping the given headings.\n"
    "- 3 to 4 full paragraphs (roughly 250-350 words) per section of real, specific, "
    "factual explanatory prose about the requested subject.\n"
    "- Never describe what a section will do; write the section itself.\n"
    "- 'bullets' is optional: 2 to 5 short key points when a section benefits.\n"
    "- Keep the JSON valid: escape newlines, no trailing commas."
)


def looks_like_description_only(text: str) -> bool:
    """True when a body merely *describes* a document instead of being it."""
    body = (text or "").strip().lower()
    if len(body) < 40:
        return True
    markers = (
        "this report will",
        "this document will",
        "the content will",
        "the report will cover",
        "the document will cover",
        "will cover",
        "will discuss",
        "will explore",
        "will include",
        "will provide an overview",
        "a detailed technical report for",
        "this report provides",
        "overview of the topics",
        "the following topics",
    )
    hits = sum(1 for marker in markers if marker in body)
    if hits >= 2:
        return True
    return len(body) < 700 and any(marker in body for marker in markers)


def clean_document_title(prompt: str) -> str:
    """Derive a document title from a natural language request."""
    text = (prompt or "").strip()
    # "write a report for my course, write a detailed report on MARIE in word"
    # -> use the clause that actually names the subject.
    segments = [seg.strip() for seg in re.split(r"[,;]", text) if seg.strip()]
    for segment in reversed(segments):
        low = segment.lower()
        if len(segment) > 12 and any(
            re.search(rf"\b{verb}\b", low)
            for verb in ("write", "create", "make", "generate", "draft", "prepare", "build")
        ):
            text = segment
            break
    # The optional word run before the noun tolerates description adjectives
    # (including misspelled ones, e.g. "write a detaile report on MARIE").
    text = re.sub(
        r"(?i)^(please\s+)?(can you\s+)?(write|create|make|generate|draft|prepare|build)\s+"
        r"(me\s+)?(a|an|the)?\s*"
        r"(?:[A-Za-z]{3,}\s+){0,2}"
        r"(report|document|docx|word document|word file|essay|paper|assignment|letter|memo|notes|write[- ]?up)\s*"
        r"(on|about|for|covering|regarding|of)?\s*",
        "",
        text,
    ).strip()
    text = re.sub(
        r"(?i)\s+(in|as|into|to)\s+(a\s+)?(word|docx|ms word|microsoft word|pdf)\b.*$",
        "",
        text,
    ).strip()
    text = text.strip(" .:;,-")
    text = re.sub(r"^(?:to|for|about|on|of)\s+", "", text, flags=re.IGNORECASE).strip()
    if not text:
        return ""
    if len(text) > 90:
        text = text[:90].rsplit(" ", 1)[0]
    return text[:1].upper() + text[1:]


def _fallback_document_body(title: str, prompt: str) -> str:
    """Last-resort content so a document is never a one-line stub."""
    subject = title or clean_document_title(prompt) or "this subject"
    parts = [
        (heading, _section_fallback(subject, heading, ""))
        for heading in DEFAULT_SECTION_HEADINGS
    ]
    return "\n\n".join(f"{heading}\n\n{body}" for heading, body in parts)


def _sections_from_data(data) -> list:
    sections: list = []
    if not isinstance(data, dict):
        return sections
    raw = data.get("sections") or data.get("paragraphs") or []
    if isinstance(raw, dict):
        raw = [raw]
    for entry in raw or []:
        if isinstance(entry, dict):
            sections.append({
                "heading": str(entry.get("heading") or entry.get("title") or "").strip(),
                "body": str(entry.get("body") or entry.get("content") or entry.get("text") or "").strip(),
                "bullets": [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()],
            })
        elif isinstance(entry, str) and entry.strip():
            sections.append({"heading": "", "body": entry.strip(), "bullets": []})
    return [s for s in sections if s.get("body") or s.get("bullets")]


# -- tolerant parsing --------------------------------------------------------

def _loads_tolerant(raw) -> Optional[dict]:
    """Parse JSON that a model may have wrapped in prose or code fences."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = _json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = _json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


# -- provider fallbacks (custom providers, Jeff routed) ----------------------

def _routed_custom_providers() -> list:
    try:
        from core import provider_registry
        return [p for p in provider_registry.configured_providers() if p.get("custom")]
    except Exception:
        return []


def _custom_chat(prompt: str, system: str, json_mode: bool = False) -> Optional[str]:
    """Ask the best-matching custom provider (chosen by Jeff) to answer."""
    providers = _routed_custom_providers()
    if not providers:
        return None
    try:
        from core import jeff_router, provider_registry

        try:
            client = jeff_router.client_from_settings()
        except Exception:
            client = None
        decision = jeff_router.route(f"{system}\n\n{prompt}", client=client, providers=providers)
    except Exception:
        decision = None
    ordered = [decision.provider] if decision is not None else []
    ordered += [p for p in providers if p not in ordered]
    for provider in ordered:
        try:
            raw = provider_registry.chat(
                provider,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=8192,
            )
        except Exception as exc:
            print(f"[DocumentGen] {provider.get('name')} failed: {exc}")
            continue
        if raw and raw.strip():
            if not json_mode:
                return raw.strip()
            parsed = _loads_tolerant(raw)
            if parsed is not None:
                return raw
    return None


#: Rate-limit wording that means "asking Gemini again is pointless right now".
_QUOTA_MARKERS = (
    "429", "resource_exhausted", "resource exhausted", "quota",
    "rate limit", "rate_limit", "too many requests",
)


class _Budget:
    """Tracks which backends may still be used during one document run.

    A rate limit is not a failure to retry: hammering the same provider with
    seven more section requests wastes the user's daily quota and takes
    minutes. Once Gemini answers 429, the run switches to another provider.
    """

    def __init__(self) -> None:
        self.gemini_blocked = False
        self.gemini_error = ""
        self.used_provider = ""

    def note_gemini_failure(self, message: str) -> None:
        self.gemini_error = message or ""
        if is_quota_error(message):
            self.gemini_blocked = True

    def note_provider(self, name: str) -> None:
        self.used_provider = name

    @property
    def any_ai_wrote(self) -> bool:
        return bool(self.used_provider)

    @property
    def reason(self) -> str:
        if self.gemini_blocked:
            return "Gemini's quota is used up"
        if self.gemini_error:
            return f"Gemini could not answer ({self.gemini_error[:80]})"
        return "no AI provider is configured"


def is_quota_error(err) -> bool:
    text = str(err or "").lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


def _local_model_name() -> str:
    try:
        from core.user_paths import get_user_data_dir
        path = get_user_data_dir() / "config" / "app_settings.json"
        if path.exists():
            data = _json.loads(path.read_text(encoding="utf-8"))
            name = str(data.get("local_ai_model") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return "qwen2.5:3b"


def _local_chat(prompt: str, system: str) -> Optional[str]:
    """Last resort: a locally hosted model (Ollama / LM Studio).

    This keeps documents real - and free - when every cloud key is out of
    quota or unset.
    """
    try:
        from core.local_brain import local_brain

        if not local_brain.is_available():
            return None
        res = local_brain.chat_complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            model=_local_model_name(),
            temperature=0.3,
        )
        text = ""
        try:
            text = str(res.get("choices", [{}])[0].get("message", {}).get("content", "") or "")
        except Exception:
            text = ""
        text = text.strip()
        return text or None
    except Exception as exc:
        print(f"[DocumentGen] local model unavailable: {exc}")
        return None


def _gemini_json(prompt: str, system: str, timeout: int, budget=None) -> Optional[dict]:
    if budget is not None and budget.gemini_blocked:
        return None
    try:
        from actions import office_generator
        from actions.office_generator import _call_gemini_json
    except Exception:
        return None
    try:
        data = _call_gemini_json(prompt, system, models=DOCUMENT_MODELS, timeout=timeout)
        if isinstance(data, dict):
            if budget is not None:
                budget.note_provider("Gemini")
            return data
        if budget is not None:
            budget.note_gemini_failure(getattr(office_generator, "LAST_ERROR", "") or "")
        return None
    except Exception as exc:
        if budget is not None:
            budget.note_gemini_failure(str(exc))
        print(f"[DocumentGen] Gemini call failed: {exc}")
        return None


def _ask_json(prompt: str, system: str, timeout: int, budget=None) -> Optional[dict]:
    """Structured answer from whichever AI backend is reachable."""
    data = _gemini_json(prompt, system, timeout, budget)
    if isinstance(data, dict):
        return data
    raw = _custom_chat(prompt, system, json_mode=True)
    if raw:
        if budget is not None:
            budget.note_provider("a custom provider")
        return _loads_tolerant(raw)
    return None


def _ask_text(prompt: str, system: str, budget=None) -> Optional[str]:
    """Plain-prose answer, used when JSON mode is unavailable or unreliable."""
    data = _gemini_json(prompt, system, SECTION_TIMEOUT, budget)
    if isinstance(data, dict):
        for key in ("body", "content", "text", "section"):
            value = str(data.get(key) or "").strip()
            if len(value) > 80:
                return value
    raw = _custom_chat(prompt, system, json_mode=False)
    if raw and len(raw.strip()) > 80:
        if budget is not None:
            budget.note_provider("a custom provider")
        return _strip_fence(raw.strip())
    text = _local_chat(prompt, system)
    if text and len(text.strip()) > 80:
        if budget is not None:
            budget.note_provider("the local model")
        return _strip_fence(text.strip())
    return None


def _strip_fence(text: str) -> str:
    fence = re.search(r"```(?:json|markdown)?\s*(.+?)```", text, re.DOTALL)
    body = fence.group(1).strip() if fence else text
    body = re.sub(r'^\s*\{\s*"body"\s*:\s*"', "", body)
    body = re.sub(r'"\s*,?\s*"bullets".*$', "", body, flags=re.DOTALL)
    body = body.replace("\\n", "\n").replace('\\"', '"')
    return body.strip().rstrip("}").strip()


# -- outline + sections ------------------------------------------------------

def _planned_sections(outline) -> list:
    """Outline entries in order, keeping ones that only carry a brief."""
    if not isinstance(outline, dict):
        return []
    raw = outline.get("sections") or outline.get("paragraphs") or []
    if isinstance(raw, dict):
        raw = [raw]
    planned: list = []
    for entry in raw or []:
        if isinstance(entry, dict):
            planned.append({
                "heading": str(entry.get("heading") or entry.get("title") or "").strip(),
                "brief": str(
                    entry.get("brief") or entry.get("summary") or entry.get("description") or ""
                ).strip(),
                "body": str(
                    entry.get("body") or entry.get("content") or entry.get("text") or ""
                ).strip(),
                "bullets": [str(b).strip() for b in (entry.get("bullets") or []) if str(b).strip()],
            })
        elif isinstance(entry, str) and entry.strip():
            planned.append({"heading": "", "brief": "", "body": entry.strip(), "bullets": []})
    return planned


def _default_outline(prompt: str, title: str = "") -> dict:
    subject = title or clean_document_title(prompt) or "the requested subject"
    return {
        "title": subject,
        "subtitle": "",
        "sections": [
            {"heading": heading, "brief": _default_brief(subject, heading)}
            for heading in DEFAULT_SECTION_HEADINGS
        ],
    }


def _default_brief(subject: str, heading: str) -> str:
    """A usable brief for an outline the model never produced."""
    focus = heading.split(" ", 1)[-1].strip().lower() or heading.lower()
    return {
        "introduction": f"What {subject} is, why it matters, and how the document is organised.",
        "background": f"The history, origins and the problem {subject} was created to solve.",
        "core concepts": f"The key definitions and terminology of {subject}, each defined precisely.",
        "detailed analysis": f"How the parts of {subject} work and interact, and the trade-offs involved.",
        "worked examples": f"Concrete worked examples showing {subject} applied step by step.",
        "applications": f"Where {subject} is used in practice, and what it is used for.",
        "conclusion": f"The main findings about {subject} and what to study next.",
    }.get(focus, f"The essential material about {focus} within {subject}.")


def _generate_outline(prompt: str, budget=None) -> dict:
    """Plan the document: title, subtitle and section headings."""
    outline = _ask_json(
        "Plan a detailed document for this request:\n"
        f"{prompt}\n\n"
        "Give the document a specific title and 6-8 sections with briefs.",
        OUTLINE_SYSTEM_INSTRUCTION,
        60,
        budget,
    )
    if not isinstance(outline, dict):
        return _default_outline(prompt)
    sections = outline.get("sections") or outline.get("paragraphs") or []
    if not isinstance(sections, list) or not sections:
        return _default_outline(prompt, str(outline.get("title") or "").strip())
    return outline


#: Role-aware placeholder prose, used only when no AI backend can be reached at
#: all. Each section gets its own text so the document still reads as a
#: document rather than the same paragraph seven times.
_SECTION_FALLBACKS = (
    ("introduction",
     "This report examines {topic}. It sets out what the subject is, why it matters, and "
     "the terminology used throughout, so the sections that follow can be read in order "
     "or dipped into individually.\n\n"
     "The scope is deliberately practical: definitions first, then how the parts fit "
     "together, then worked examples and applications. Where a term is used in more than "
     "one sense in the literature, the sense used here is stated explicitly."),
    ("background",
     "The story of {topic} starts with the problem it was designed to solve rather than "
     "with its definition. Earlier approaches ran into limits - cost, scale or precision - "
     "and the ideas behind {topic} grew out of attempts to get past those limits.\n\n"
     "Placing the subject in that context matters because most of its design choices are "
     "answers to constraints that are easy to miss when the topic is met for the first "
     "time. This section sets out that context and marks which parts are still current."),
    ("core concept",
     "The central ideas of {topic} are introduced here, together with the vocabulary used "
     "for the rest of the document. Each term is defined before it is used, and related "
     "terms are contrasted so they are not confused later.\n\n"
     "The core mechanism is then described at the level of detail needed to reason about "
     "it: what the components are, what each one does, and what is assumed about them."),
    ("analysis",
     "This section examines {topic} in more depth, looking at how the components interact "
     "and why particular design choices are made. Each choice is weighed against the "
     "alternatives so the trade-offs - and the situations in which they matter - are clear.\n\n"
     "Where two approaches compete, the criteria that decide between them are stated "
     "explicitly rather than left implicit, and the conditions under which each is the "
     "better choice are identified."),
    ("example",
     "Concrete examples make the theory of {topic} checkable. The examples here are worked "
     "through step by step, starting from the problem statement and finishing at the "
     "result, so each step can be followed and reproduced.\n\n"
     "The reasoning behind each step is spelled out: why that step comes next, what "
     "assumption it relies on, and what would change if the assumption did not hold."),
    ("application",
     "The practical uses of {topic} are surveyed here, showing where the concepts matter "
     "in real systems and workflows rather than only in theory. Typical uses are described "
     "alongside the constraints that govern them.\n\n"
     "Limits are included as well as strengths: knowing where {topic} stops being the "
     "right tool is as useful as knowing where it works well."),
    ("conclusion",
     "The report closes by drawing together the main points about {topic}: what it is, how "
     "it works, where it is used and what it costs. The aim is a short, accurate summary "
     "that can be read on its own.\n\n"
     "Areas where further study would be useful are noted, together with the sources or "
     "lines of enquiry that would repay attention first."),
)


def _section_fallback(subject: str, heading: str, brief: str) -> str:
    """Last-resort prose for one section when no AI backend can be reached."""
    topic = subject or "the subject"
    low = (heading or "").lower()
    for key, text in _SECTION_FALLBACKS:
        if key in low:
            return text.format(topic=topic)
    return (
        _SECTION_FALLBACKS[2][1].format(topic=topic)
    )


def _sections_from_batch(data, batch: list) -> list:
    """Map a batched answer back onto the planned headings."""
    produced = _sections_from_data(data) if isinstance(data, dict) else []
    out: list = []
    for position, planned in enumerate(batch):
        entry = produced[position] if position < len(produced) else None
        body = str((entry or {}).get("body") or "").strip()
        if len(body) < 120:
            out.append(None)
            continue
        out.append({
            "heading": planned.get("heading") or f"Section {position + 1}",
            "body": body,
            "bullets": list((entry or {}).get("bullets") or []),
        })
    return out


def _generate_batch(subject: str, batch: list, start: int, total: int, budget=None) -> list:
    """Write several sections in one request - fewer calls, less quota spent."""
    listing = "\n".join(
        f"{start + offset}. {entry.get('heading') or 'Section'}: "
        f"{entry.get('brief') or 'the topic suggested by the heading'}"
        for offset, entry in enumerate(batch)
    )
    prompt = (
        f"Document subject: {subject}\n"
        f"Write sections {start}-{start + len(batch) - 1} of {total}, in this order:\n"
        f"{listing}\n\n"
        "Write the finished prose for each of those sections now."
    )
    data = _ask_json(prompt, SECTION_BATCH_SYSTEM_INSTRUCTION, SECTION_TIMEOUT, budget)
    return _sections_from_batch(data, batch)


def _generate_section(subject: str, heading: str, brief: str, index: int, total: int,
                      budget=None) -> dict:
    """Write one section of the document (the section itself, not a plan)."""
    prompt = (
        f"Document subject: {subject}\n"
        f"Section {index} of {total}: {heading}\n"
        f"What this section must cover: {brief or 'the topic suggested by the heading'}\n\n"
        f"Write the finished prose for this section now."
    )
    data = _ask_json(prompt, SECTION_SYSTEM_INSTRUCTION, SECTION_TIMEOUT, budget)
    body = ""
    bullets: list[str] = []
    if isinstance(data, dict):
        body = str(data.get("body") or data.get("content") or data.get("text") or "").strip()
        raw_bullets = data.get("bullets") or []
        if isinstance(raw_bullets, str):
            raw_bullets = [b for b in re.split(r"[\n;]", raw_bullets)]
        bullets = [str(b).strip("-•* \t") for b in raw_bullets if str(b).strip()]
    if len(body) < 80:
        text = _ask_text(prompt, SECTION_SYSTEM_INSTRUCTION, budget)
        if text and len(text) > len(body):
            body = text
            bullets = []
    if not body:
        body = _section_fallback(subject, heading, brief)
    return {"heading": heading, "body": body, "bullets": bullets}


def _report_progress(player, done: int, total: int, label: str = "") -> None:
    """Show visible progress while a multi-call document is being written."""
    total = max(total, 1)
    percent = int(min(done, total) / total * 100)
    if player is None:
        return
    try:
        if hasattr(player, "update_task_workspace"):
            player.update_task_workspace(
                status=f"Writing document - section {min(done + 1, total)} of {total}",
                output=f"Working on: {label}" if label else "Writing the document content.",
                percent=percent,
            )
    except Exception:
        pass
    if done and hasattr(player, "write_log"):
        try:
            player.write_log(f"SYS: Document section {done}/{total} written.")
        except Exception:
            pass


def generate_document_from_prompt(
    user_prompt: str,
    player=None,
    speak: Optional[Callable[[str], None]] = None,
    title: str | None = None,
    subtitle: str | None = None,
    author: str | None = None,
    subject: str | None = None,
    output_path: str | None = None,
) -> str:
    """Create a Word (.docx) document with fully written-out content."""
    from actions.docx_tools import word_document

    prompt = (user_prompt or "").strip()
    if speak:
        speak("Writing the full document content now, sir...")

    budget = _Budget()

    # Pass 1: outline (small request, always fits).
    outline = _generate_outline(prompt, budget)
    doc_title = (title or "").strip() or str(outline.get("title") or "").strip() \
        or clean_document_title(prompt) or "NIGHTFALL AI Document"
    doc_subtitle = (subtitle or "").strip() or str(outline.get("subtitle") or "").strip()

    planned = _planned_sections(outline)
    if not planned:
        planned = _planned_sections(_default_outline(prompt, doc_title))
    planned = planned[:9]

    # Pass 2: sections written in small batches (never one giant request, which
    # is truncated into invalid JSON) - and no further Gemini calls once the
    # provider has said its quota is exhausted.
    sections: list[dict] = []
    total = len(planned)
    subject = doc_title or clean_document_title(prompt)
    _report_progress(player, 0, total, doc_title)

    for start in range(0, total, SECTIONS_PER_CALL):
        batch = planned[start:start + SECTIONS_PER_CALL]
        _report_progress(player, start, total, str(batch[0].get("heading") or doc_title))
        written_batch: list = [None] * len(batch)
        if not any(str(entry.get("body") or "").strip() for entry in batch):
            written_batch = _generate_batch(subject, batch, start + 1, total, budget)
        for offset, planned_section in enumerate(batch):
            index = start + offset + 1
            heading = planned_section.get("heading") or f"Section {index}"
            written = written_batch[offset]
            if written is None:
                body = str(planned_section.get("body") or "").strip()
                bullets = list(planned_section.get("bullets") or [])
                if len(body) < 80:
                    one = _generate_section(
                        subject,
                        heading,
                        str(planned_section.get("brief") or planned_section.get("summary") or ""),
                        index,
                        total,
                        budget,
                    )
                    heading = heading or one.get("heading", "")
                    body = one["body"]
                    bullets = one["bullets"]
            else:
                heading = written.get("heading") or heading
                body = written["body"]
                bullets = written["bullets"]
            sections.append({"heading": heading, "body": body, "bullets": bullets})
            _report_progress(player, index, total, heading)

    if not budget.any_ai_wrote and player is not None and hasattr(player, "write_log"):
        # Never let placeholder prose masquerade as a written report.
        try:
            player.write_log(
                "System Event: Document written with placeholder text - "
                f"{budget.reason}. Add a key in Settings, Custom AI Providers "
                "(Groq, OpenRouter, ...) for a fully written report."
            )
        except Exception:
            pass

    params = {"action": "create_report", "title": doc_title, "auto_open": True}
    if doc_subtitle:
        params["subtitle"] = doc_subtitle
    if author:
        params["author"] = author
    if subject:
        params["subject"] = subject
    if output_path:
        params["output_path"] = output_path

    if sections and any(str(s.get("body") or "").strip() for s in sections):
        params["sections"] = sections
    else:
        # Nothing at all came back from any backend: still write a complete
        # document rather than a one-line stub.
        params["content"] = _fallback_document_body(doc_title, prompt)

    return word_document(parameters=params, player=player, speak=speak)
