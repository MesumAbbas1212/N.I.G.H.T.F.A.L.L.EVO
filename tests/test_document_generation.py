"""Word document requests must produce a real document, not a description.

Regression tests for the bug where "write a detailed report on MARIE in word
document" produced only a paragraph describing what the report *would* contain.
The document router, the description detector, and the .docx writer are all
exercised here (the model call is stubbed, so no API key is needed).
"""

import re

import pytest

from actions import docx_tools
from actions.document_generator import (
    clean_document_title,
    looks_like_description_only,
)

REPORT_REQUEST = (
    "write a report for my computer organization and assembly language course, "
    "write a detailed report on MARIE in word document"
)

DESCRIPTION_ONLY = (
    "A detailed technical report for the Computer Organization and Assembly "
    "Language course. The content will cover key architectural concepts including "
    "CPU structure, control unit design, memory hierarchy (cache, main memory, "
    "virtual memory), instruction set architecture (ISA), data paths, and "
    "fundamental assembly language programming principles such as registers, "
    "addressing modes, and basic command structure for effective low-level system "
    "interaction."
)

REAL_REPORT_BODY = (
    "MARIE (Machine Architecture that is Really Intuitive and Easy) is a "
    "simplified von Neumann architecture used for teaching. It has a single "
    "accumulator (AC), a memory address register (MAR), a memory buffer register "
    "(MBR), a program counter (PC), an instruction register (IR), an input "
    "register (InREG) and an output register (OutREG). Memory consists of 4096 "
    "words of 16 bits each, and instructions are also 16 bits: the top four bits "
    "hold the opcode and the remaining twelve bits hold the operand address."
)


# -- request routing ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "write a report for my computer organization and assembly language course, "
    "write a detailed report on MARIE in word document",
    "create a word document about MARIE",
    "make a detailed report on MARIE",
    "draft an essay on the MARIE architecture in a docx",
    "write me a letter to my professor",
    "please write a report on MARIE",
    "prepare an assignment on MARIE in ms word",
])
def test_document_requests_are_detected(text):
    import main

    assert main._looks_like_document_request(text)


@pytest.mark.parametrize("text", [
    "open my report.docx",
    "read this document",
    "summarize the report on my desktop",
    "convert this document to pdf",
    "edit the letter I wrote yesterday",
    "what should I put in my report on MARIE?",
    "how do I write a report",
    "where is my word document",
    "open word",
    "show me the document",
])
def test_read_and_open_requests_are_not_create_requests(text):
    import main

    assert not main._looks_like_document_request(text)


def test_dictated_body_is_left_to_the_normal_path():
    import main

    dictated = 'write a word document with this text: "' + (REAL_REPORT_BODY * 8) + '"'
    assert not main._looks_like_document_request(dictated)


# -- description detection ---------------------------------------------------

def test_description_only_bodies_are_detected():
    assert looks_like_description_only(DESCRIPTION_ONLY)
    assert looks_like_description_only("")
    assert looks_like_description_only("Add report content here.")


def test_real_report_content_is_not_treated_as_a_description():
    assert not looks_like_description_only(REAL_REPORT_BODY)


def test_title_is_derived_from_the_request():
    assert clean_document_title(REPORT_REQUEST) == "MARIE"
    assert clean_document_title("create a word document about cache coherence") == "Cache coherence"


# -- the docx writer refuses to save a stub ----------------------------------

def test_missing_or_described_content_must_be_generated():
    assert docx_tools.document_needs_generated_content({"action": "create", "title": "MARIE"})
    assert docx_tools.document_needs_generated_content(
        {"action": "create_report", "title": "MARIE", "content": DESCRIPTION_ONLY}
    )


def test_real_content_and_other_actions_are_kept():
    assert not docx_tools.document_needs_generated_content(
        {"action": "create_report", "title": "MARIE", "content": REAL_REPORT_BODY}
    )
    assert not docx_tools.document_needs_generated_content(
        {"action": "create_report", "title": "MARIE",
         "sections": [{"heading": "Intro", "body": "..."}]}
    )
    assert not docx_tools.document_needs_generated_content({"action": "read", "file_path": "x.docx"})
    assert not docx_tools.document_needs_generated_content(
        {"action": "append", "file_path": "x.docx", "content": "more text"}
    )
    assert not docx_tools.document_needs_generated_content(
        {"action": "create", "doc_type": "letter", "content": "Dear Sir"}
    )


# -- end to end: the file on disk must contain the report --------------------

MODEL_REPORT = {
    "title": "MARIE: A Detailed Report on Computer Organization and Assembly Language",
    "subtitle": "Computer Organization and Assembly Language",
    "sections": [
        {
            "heading": "1. Introduction",
            "body": (
                "MARIE (Machine Architecture that is Really Intuitive and Easy) is a "
                "pedagogical von Neumann architecture used to teach the fundamentals of "
                "computer organization. It models the essential datapath of a real CPU "
                "with a deliberately small instruction set so that the "
                "fetch-decode-execute cycle can be studied end to end.\n\n"
                "This report examines the registers, memory organisation, instruction "
                "set and assembly language of MARIE."
            ),
            "bullets": ["Von Neumann model", "16-bit word size", "15 instructions"],
        },
        {
            "heading": "2. Register Set",
            "body": (
                "MARIE has seven registers: the accumulator (AC), memory address "
                "register (MAR), memory buffer register (MBR), program counter (PC), "
                "instruction register (IR), input register (InREG) and output register "
                "(OutREG). The AC holds data and intermediate results."
            ),
            "bullets": ["AC - accumulator", "MAR - memory address register"],
        },
        {
            "heading": "3. Instruction Set",
            "body": (
                "Each MARIE instruction is 16 bits: a 4-bit opcode followed by a 12-bit "
                "operand address, which allows 4096 words of memory to be addressed. "
                "The instruction set contains Load, Store, Add, Subt, Input, Output, "
                "Halt, Skipcond, Jump and the indirect variants."
            ),
            "bullets": ["Opcode 0001 = Load", "Opcode 0111 = Halt"],
        },
    ],
}


def _read_docx(path):
    from docx import Document

    return "\n".join(p.text for p in Document(str(path)).paragraphs)


def test_document_request_without_content_writes_the_full_report(tmp_path, monkeypatch):
    pytest.importorskip("docx")
    import actions.office_generator as office_generator

    monkeypatch.setattr(office_generator, "_call_gemini_json", lambda *a, **k: MODEL_REPORT)

    out = tmp_path / "marie_report.docx"
    result = docx_tools.word_document({
        "action": "create",
        "title": "MARIE Report",
        "topic": REPORT_REQUEST,
        "output_path": str(out),
        "open_after": False,
    })

    assert out.exists(), result
    text = _read_docx(out)
    assert "MARIE" in text
    for heading in ("1. Introduction", "2. Register Set", "3. Instruction Set"):
        assert heading in text
    assert "fetch-decode-execute" in text
    assert "Opcode 0001 = Load" in text
    assert "the content will cover" not in text.lower()
    assert len(text) > 800


def test_described_content_is_replaced_by_the_real_report(tmp_path, monkeypatch):
    pytest.importorskip("docx")
    import actions.office_generator as office_generator

    monkeypatch.setattr(office_generator, "_call_gemini_json", lambda *a, **k: MODEL_REPORT)

    out = tmp_path / "described.docx"
    result = docx_tools.word_document({
        "action": "create_report",
        "title": "Detailed Computer Organization and Assembly Language Report",
        "content": DESCRIPTION_ONLY,
        "topic": REPORT_REQUEST,
        "output_path": str(out),
        "open_after": False,
    })

    assert out.exists(), result
    text = _read_docx(out)
    assert "fetch-decode-execute" in text
    assert "the content will cover" not in text.lower()


def test_supplied_content_is_kept_verbatim(tmp_path):
    pytest.importorskip("docx")

    out = tmp_path / "verbatim.docx"
    result = docx_tools.word_document({
        "action": "create_report",
        "title": "My Notes",
        "content": REAL_REPORT_BODY,
        "output_path": str(out),
        "open_after": False,
    })

    assert out.exists(), result
    text = _read_docx(out)
    assert REAL_REPORT_BODY in text
    assert "Introduction" not in text


def test_document_is_still_written_when_the_model_is_unreachable(tmp_path, monkeypatch):
    pytest.importorskip("docx")
    import actions.office_generator as office_generator

    monkeypatch.setattr(office_generator, "_call_gemini_json", lambda *a, **k: None)

    out = tmp_path / "fallback.docx"
    result = docx_tools.word_document({
        "action": "create",
        "title": "MARIE Report",
        "topic": REPORT_REQUEST,
        "output_path": str(out),
        "open_after": False,
    })

    assert out.exists(), result
    text = _read_docx(out)
    assert "1. Introduction" in text
    assert "7. Conclusion" in text
    assert "MARIE" in text


# -- chunked generation ------------------------------------------------------
# Asking one model call for a whole multi-page document used to blow the
# output-token ceiling, return truncated (invalid) JSON, and leave the user
# with the filler template.  Documents are now written outline-first, then one
# call per section.

def test_title_survives_a_misspelled_adjective():
    assert clean_document_title("write a detaile report on MARIE") == "MARIE"
    assert clean_document_title("Write a detailed report on MARIE") == "MARIE"
    assert clean_document_title("create a word document about cache coherence") == "Cache coherence"


def _capture_word_document(monkeypatch):
    import actions.docx_tools as docx_tools

    captured = {}

    def fake_word_document(parameters=None, player=None, speak=None):
        captured.update(parameters or {})
        return "Created the document."

    monkeypatch.setattr(docx_tools, "word_document", fake_word_document)
    return captured


def test_each_section_is_written_by_its_own_call(monkeypatch):
    import actions.document_generator as dg

    prompts = []

    def fake_gemini_json(prompt, system, timeout):
        prompts.append((prompt, system))
        if "Plan a detailed document" in prompt:
            return {
                "title": "MARIE Report",
                "sections": [
                    {"heading": "1. Introduction", "brief": "what MARIE is"},
                    {"heading": "2. The Register Set", "brief": "AC, MAR, MBR"},
                    {"heading": "3. Conclusion", "brief": "summary"},
                ],
            }
        return {"body": "Real prose about MARIE. " * 20, "bullets": ["16-bit words"]}

    monkeypatch.setattr(dg, "_gemini_json", fake_gemini_json)
    captured = _capture_word_document(monkeypatch)

    dg.generate_document_from_prompt("write a detailed report on MARIE")

    # one outline call + one call per section, never a single giant request
    assert len(prompts) == 4
    assert len(captured["sections"]) == 3
    assert captured["title"] == "MARIE Report"
    for section in captured["sections"]:
        assert len(section["body"]) > 200
        assert "Real prose about MARIE" in section["body"]
    assert captured["sections"][0]["bullets"] == ["16-bit words"]


def test_sections_are_written_even_without_json_mode(monkeypatch):
    import actions.document_generator as dg

    def fake_gemini_json(prompt, system, timeout):
        if "Plan a detailed document" in prompt:
            return {"title": "MARIE", "sections": [{"heading": "1. Introduction", "brief": "x"}]}
        return None  # JSON mode failed for the section

    def fake_ask_text(prompt, system):
        return "Plain prose fallback for the section, written out at length. " * 4

    monkeypatch.setattr(dg, "_gemini_json", fake_gemini_json)
    monkeypatch.setattr(dg, "_ask_text", fake_ask_text)
    captured = _capture_word_document(monkeypatch)

    dg.generate_document_from_prompt("write a report on MARIE")

    assert "Plain prose fallback" in captured["sections"][0]["body"]


def test_outline_failure_still_produces_subject_aware_sections(monkeypatch):
    import actions.document_generator as dg

    monkeypatch.setattr(dg, "_gemini_json", lambda *a, **k: None)
    monkeypatch.setattr(dg, "_ask_text", lambda *a, **k: None)
    captured = _capture_word_document(monkeypatch)

    dg.generate_document_from_prompt("write a detailed report on MARIE")

    headings = [s["heading"] for s in captured["sections"]]
    assert headings[0] == "1. Introduction"
    assert headings[-1] == "7. Conclusion"
    body = " ".join(s["body"] for s in captured["sections"])
    assert "MARIE" in body
    assert "this report will" not in body.lower()


def test_tolerant_json_parsing_handles_fences_and_prose():
    import actions.document_generator as dg

    assert dg._loads_tolerant('```json\n{"body": "hi"}\n```') == {"body": "hi"}
    assert dg._loads_tolerant('Sure! Here you go:\n{"body": "hi"}\nHope that helps') == {"body": "hi"}
    assert dg._loads_tolerant("not json at all") is None


def test_chunked_generation_reaches_the_docx_on_disk(tmp_path, monkeypatch):
    """The end-to-end path: request -> outline -> sections -> real .docx."""
    pytest.importorskip("docx")
    import actions.document_generator as dg

    def fake_gemini_json(prompt, system, timeout):
        if "Plan a detailed document" in prompt:
            return {
                "title": "MARIE: A Detailed Report",
                "subtitle": "Computer Organization and Assembly Language",
                "sections": [
                    {"heading": "1. Introduction", "brief": "what MARIE is"},
                    {"heading": "2. Register Set", "brief": "AC, MAR, MBR, PC"},
                    {"heading": "3. Conclusion", "brief": "summary of the report"},
                ],
            }
        if "Register Set" in prompt:
            return {
                "body": (
                    "MARIE has seven registers that make the fetch-decode-execute cycle "
                    "visible to a student. The accumulator holds operands and results, the "
                    "memory address register carries the address being read or written, and "
                    "the memory buffer register holds the word that travels to or from RAM."
                ),
                "bullets": ["AC holds intermediate results", "MAR addresses memory"],
            }
        return {
            "body": (
                "This section explains the machine architecture that is really intuitive "
                "and easy, known as MARIE, in the detail a course report requires. The "
                "architecture is a von Neumann machine with a single 16-bit bus, so every "
                "transfer can be followed one cycle at a time."
            )
        }

    monkeypatch.setattr(dg, "_gemini_json", fake_gemini_json)

    out = tmp_path / "marie_chunked.docx"
    result = docx_tools.word_document({
        "action": "create",
        "topic": "write a detaile report on MARIE",
        "output_path": str(out),
        "open_after": False,
    })

    assert out.exists(), result
    text = _read_docx(out)
    assert "MARIE: A Detailed Report" in text
    for heading in ("1. Introduction", "2. Register Set", "3. Conclusion"):
        assert heading in text
    assert "fetch-decode-execute" in text
    assert "AC holds intermediate results" in text
    # The filler template must never appear when a model answered.
    assert "It is intended as a complete reference" not in text
    assert len(text) > 700


def test_document_writing_reports_progress(monkeypatch):
    """A multi-call document takes a while - the user must see it moving."""
    import actions.document_generator as dg

    def fake_gemini_json(prompt, system, timeout):
        if "Plan a detailed document" in prompt:
            return {"title": "T", "sections": [{"heading": "1. One"}, {"heading": "2. Two"}]}
        return {"body": "Long enough section prose. " * 10}

    monkeypatch.setattr(dg, "_gemini_json", fake_gemini_json)
    _capture_word_document(monkeypatch)

    class FakePlayer:
        def __init__(self):
            self.updates = []
            self.logs = []

        def update_task_workspace(self, **kwargs):
            self.updates.append(kwargs)

        def write_log(self, message):
            self.logs.append(message)

    player = FakePlayer()
    dg.generate_document_from_prompt("write a report on MARIE", player=player)

    assert len(player.updates) >= 3
    assert "section" in player.updates[0]["status"].lower()
    assert player.updates[-1]["percent"] == 100
    assert any("2/2" in line for line in player.logs)
