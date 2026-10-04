"""Full-content generation for Word documents.

When the user asks for a document ("write a detailed report on MARIE in a word
document") the assistant must produce the *document*, not a description of it.
This module writes the complete body (title, sections, prose) and hands it to
``actions.docx_tools.word_document`` so a real .docx lands on disk.
"""

from __future__ import annotations

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
    text = re.sub(
        r"(?i)^(please\s+)?(can you\s+)?(write|create|make|generate|draft|prepare|build)\s+"
        r"(me\s+)?(a|an|the)?\s*(detailed|full|comprehensive|complete|long|short|brief)?\s*"
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
    subject = clean_document_title(prompt) or title or "this subject"
    parts = [
        ("1. Introduction", f"This report examines {subject}. It is intended as a complete "
                            "reference for the topic, covering its background, core concepts, "
                            "and practical relevance."),
        ("2. Background and Context", f"{subject} is studied as part of a structured body of "
                                      "knowledge. Understanding its historical development and "
                                      "the problems it was designed to solve provides the "
                                      "foundation for the discussion that follows."),
        ("3. Core Concepts", f"The central ideas of {subject} are introduced here together with "
                            "the terminology used throughout the document. Each concept is "
                            "defined precisely so later sections can build on it."),
        ("4. Detailed Analysis", f"This section analyses the components of {subject} in depth, "
                                 "explaining how they interact, why specific design choices are "
                                 "made, and what trade-offs they involve."),
        ("5. Worked Examples", "Concrete examples illustrate how the theory is applied in "
                               "practice. Each example is worked through step by step so the "
                               "reasoning can be followed and reproduced."),
        ("6. Applications", f"The practical uses of {subject} are surveyed, showing where the "
                            "concepts matter in real systems and workflows."),
        ("7. Conclusion", f"The report closes by summarising the key findings and pointing to "
                          f"areas where further study of {subject} would be valuable."),
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
    from actions.office_generator import _call_gemini_json

    prompt = (user_prompt or "").strip()
    if speak:
        speak("Writing the full document content now, sir...")

    data = _call_gemini_json(
        prompt,
        DOCUMENT_SYSTEM_INSTRUCTION,
        models=DOCUMENT_MODELS,
        timeout=DOCUMENT_TIMEOUT,
    )

    doc_title = (title or "").strip()
    doc_subtitle = (subtitle or "").strip()
    sections = _sections_from_data(data)

    if not doc_title:
        doc_title = (str(data.get("title") or "").strip() if isinstance(data, dict) else "") \
            or clean_document_title(prompt) or "NIGHTFALL AI Document"
    if not doc_subtitle and isinstance(data, dict):
        doc_subtitle = str(data.get("subtitle") or "").strip()

    params = {"action": "create_report", "title": doc_title, "auto_open": True}
    if doc_subtitle:
        params["subtitle"] = doc_subtitle
    if author:
        params["author"] = author
    if subject:
        params["subject"] = subject
    if output_path:
        params["output_path"] = output_path

    if sections:
        params["sections"] = sections
    elif isinstance(data, dict) and str(data.get("content") or "").strip():
        params["content"] = str(data.get("content")).strip()
    else:
        params["content"] = _fallback_document_body(doc_title, prompt)

    return word_document(parameters=params, player=player, speak=speak)
