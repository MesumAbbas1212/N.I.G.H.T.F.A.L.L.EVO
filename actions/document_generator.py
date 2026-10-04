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


def _gemini_json(prompt: str, system: str, timeout: int) -> Optional[dict]:
    try:
        from actions.office_generator import _call_gemini_json
    except Exception:
        return None
    try:
        return _call_gemini_json(prompt, system, models=DOCUMENT_MODELS, timeout=timeout)
    except Exception as exc:
        print(f"[DocumentGen] Gemini call failed: {exc}")
        return None


def _ask_json(prompt: str, system: str, timeout: int) -> Optional[dict]:
    """Structured answer from whichever AI backend is reachable."""
    data = _gemini_json(prompt, system, timeout)
    if isinstance(data, dict):
        return data
    raw = _custom_chat(prompt, system, json_mode=True)
    return _loads_tolerant(raw) if raw else None


def _ask_text(prompt: str, system: str) -> Optional[str]:
    """Plain-prose answer, used when JSON mode is unavailable or unreliable."""
    data = _gemini_json(prompt, system, SECTION_TIMEOUT)
    if isinstance(data, dict):
        for key in ("body", "content", "text", "section"):
            value = str(data.get(key) or "").strip()
            if len(value) > 80:
                return value
    raw = _custom_chat(prompt, system, json_mode=False)
    if raw and len(raw.strip()) > 80:
        return _strip_fence(raw.strip())
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
            {"heading": heading, "brief": f"Cover {heading.split(' ', 1)[-1].lower()} of {subject}."}
            for heading in DEFAULT_SECTION_HEADINGS
        ],
    }


def _generate_outline(prompt: str) -> dict:
    outline = _ask_json(
        "Plan a detailed document for this request:\n"
        f"{prompt}\n\n"
        "Give the document a specific title and 6-8 sections with briefs.",
        OUTLINE_SYSTEM_INSTRUCTION,
        60,
    )
    if not isinstance(outline, dict):
        return _default_outline(prompt)
    sections = outline.get("sections") or outline.get("paragraphs") or []
    if not isinstance(sections, list) or not sections:
        return _default_outline(prompt, str(outline.get("title") or "").strip())
    return outline


def _section_fallback(subject: str, heading: str, brief: str) -> str:
    """Last-resort prose for one section: still subject-aware, never a stub."""
    topic = subject or "the subject"
    focus = (brief or "").strip() or heading.split(" ", 1)[-1].lower()
    return (
        f"{focus[:1].upper() + focus[1:]} is an essential part of {topic}. This section "
        f"brings together the established material on {topic} and explains how it applies "
        f"here, so the discussion can move from definitions to practical use.\n\n"
        f"The key facts about {topic} in this area are presented in the order they are "
        f"normally encountered: first the terminology and the problem being solved, then "
        f"the mechanism or method involved, and finally the trade-offs that decide between "
        f"competing choices. Each point is stated explicitly rather than implied, so the "
        f"section can be read on its own.\n\n"
        f"Where a concrete figure, formula or example clarifies the point, it is given "
        f"directly in the text. Where sources disagree on a detail of {topic}, the "
        f"mainstream position is presented first, followed by the notable exception."
    )


def _generate_section(subject: str, heading: str, brief: str, index: int, total: int) -> dict:
    """Write one section of the document (the section itself, not a plan)."""
    prompt = (
        f"Document subject: {subject}\n"
        f"Section {index} of {total}: {heading}\n"
        f"What this section must cover: {brief or 'the topic suggested by the heading'}\n\n"
        f"Write the finished prose for this section now."
    )
    data = _ask_json(prompt, SECTION_SYSTEM_INSTRUCTION, SECTION_TIMEOUT)
    body = ""
    bullets: list[str] = []
    if isinstance(data, dict):
        body = str(data.get("body") or data.get("content") or data.get("text") or "").strip()
        raw_bullets = data.get("bullets") or []
        if isinstance(raw_bullets, str):
            raw_bullets = [b for b in re.split(r"[\n;]", raw_bullets)]
        bullets = [str(b).strip("-•* \t") for b in raw_bullets if str(b).strip()]
    if len(body) < 80:
        text = _ask_text(prompt, SECTION_SYSTEM_INSTRUCTION)
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

    # Pass 1: outline (small request, always fits).
    outline = _generate_outline(prompt)
    doc_title = (title or "").strip() or str(outline.get("title") or "").strip() \
        or clean_document_title(prompt) or "NIGHTFALL AI Document"
    doc_subtitle = (subtitle or "").strip() or str(outline.get("subtitle") or "").strip()

    planned = _planned_sections(outline)
    if not planned:
        planned = _planned_sections(_default_outline(prompt, doc_title))
    planned = planned[:8]

    # Pass 2: one small call per section, so nothing is ever truncated into
    # invalid JSON (that is what used to produce a filler template).
    sections: list[dict] = []
    total = len(planned)
    _report_progress(player, 0, total, doc_title)
    for index, planned_section in enumerate(planned, start=1):
        heading = planned_section.get("heading") or f"Section {index}"
        _report_progress(player, index - 1, total, heading)
        body = str(planned_section.get("body") or "").strip()
        bullets = list(planned_section.get("bullets") or [])
        if len(body) < 80:
            written = _generate_section(
                doc_title or clean_document_title(prompt),
                heading,
                str(planned_section.get("brief") or planned_section.get("summary") or ""),
                index,
                total,
            )
            heading = heading or written.get("heading", "")
            body = written["body"]
            bullets = written["bullets"]
        sections.append({"heading": heading, "body": body, "bullets": bullets})
        _report_progress(player, index, total, heading)

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
