"""A question must never be mistaken for a build request.

Reported bug: "what is today critical news in pakistan especially what happened
to the march that was about to ..." was routed to the NIGHTFALL Dev agent,
which started narrating raw shell commands into the chat and then apologised
for a "build error". The cause was substring keyword matching - "happened"
contains "app" - so the sentence looked like "build me an app".
"""

import importlib
import re

import pytest


@pytest.fixture()
def main_module():
    return importlib.import_module("main")


NEWS_QUESTION = (
    "what is today critical news in pakistan especially what happened to the "
    "march that was about to"
)


# -- the reported sentence ---------------------------------------------------

def test_news_question_is_not_a_code_request(main_module):
    assert main_module._looks_like_code_request(NEWS_QUESTION) is False


def test_news_question_is_not_a_website_request(main_module):
    assert main_module._looks_like_website_request(NEWS_QUESTION) is False


def test_news_question_is_not_a_document_or_deck_request(main_module):
    assert main_module._looks_like_document_request(NEWS_QUESTION) is False
    assert main_module._looks_like_presentation_request(NEWS_QUESTION) is False
    assert main_module._looks_like_spreadsheet_request(NEWS_QUESTION) is False


@pytest.mark.parametrize("question", [
    "what happened to the march in islamabad today",
    "what is happening in karachi",
    "who won the match yesterday",
    "when is the next election",
    "how does the api work",
    "explain the app store rules",
    "tell me about the new application form",
    "what is a python script",
    "why did the website go down",
])
def test_questions_are_never_build_requests(main_module, question):
    assert main_module._is_informational_question(question) is True
    assert main_module._looks_like_code_request(question) is False


# -- real build requests still reach the dev agent ---------------------------

@pytest.mark.parametrize("request_text", [
    "build me a website for my bakery",
    "create a python script to scrape prices",
    "write a function that reverses a string",
    "make a to-do app with react",
    "fix the bug in my app",
    "debug my python script",
    "build a calculator in html",
])
def test_real_build_requests_are_still_detected(main_module, request_text):
    assert main_module._looks_like_code_request(request_text) is True


def test_a_build_request_that_is_also_a_question_still_builds(main_module):
    # "can you build ..." is a question, but it asks for a build.
    assert main_module._is_informational_question("can you build me a website") is False
    assert main_module._looks_like_code_request("can you build me a website") is True


# -- word matching -----------------------------------------------------------

def test_words_are_matched_whole(main_module):
    assert main_module._word_present("app", "build an app") is True
    assert main_module._word_present("app", "what happened here") is False
    assert main_module._word_present("web", "a spider web") is True
    assert main_module._word_present("web", "a website") is False


def test_document_words_are_matched_whole(main_module):
    # a documentary is not a Word document
    assert main_module._looks_like_document_request(
        "write a documentary script about the flood"
    ) is False


def test_no_substring_build_matching_remains(main_module):
    """Guard the class of bug, not just the sentence."""
    source = open(main_module.__file__, encoding="utf-8-sig").read()
    gate = source.split("website_request = _looks_like_website_request(text)")[1]
    gate = gate.split("if website_request or code_request:")[0]
    assert " in text.lower()" not in gate


# -- the dev agent narrates summaries, not commands --------------------------

def test_dev_agent_never_speaks_a_raw_command():
    from actions import nightfall_dev_agent as dev

    spoken = []
    agent = dev.NIGHTFALLDevAgent(workspace_dir="/tmp", speak=spoken.append)
    agent._on_action('⚡ Running command: python -c "import urllib.request; print(1)"')
    agent._on_action("📝 Writing file: fetch_news.py")
    agent._on_action("✏️ Editing file: index.html")

    assert spoken, "the agent should say what it is doing"
    for line in spoken:
        assert len(line) < 80, line
        assert "urllib" not in line
        assert "import" not in line
        assert line.endswith("."), line
    assert "fetch_news.py" in spoken[1]


def test_dev_agent_falls_back_to_configured_providers(monkeypatch):
    """The dev agent used to stop at Gemini + OpenRouter."""
    from actions import nightfall_dev_agent as dev

    called = {}

    def fake_failover(messages, **kwargs):
        called["messages"] = messages
        return "done", {"id": "agnes-ai", "name": "Agnes AI"}, ["gemini: 429 RESOURCE_EXHAUSTED"]

    monkeypatch.setattr(dev, "API_CONFIG_PATH", "/nonexistent/api_keys.json")
    import core.provider_registry as reg
    monkeypatch.setattr(reg, "chat_failover", fake_failover)

    agent = dev.NIGHTFALLDevAgent(workspace_dir="/tmp")
    agent.history = [
        {"role": "system", "content": "you are a coder"},
        {"role": "user", "content": "build a page"},
    ]
    assert agent._call_llm() == "done"
    assert called["messages"][0]["content"] == "you are a coder"


def test_dev_agent_error_names_the_next_step(monkeypatch):
    from actions import nightfall_dev_agent as dev

    monkeypatch.setattr(dev, "API_CONFIG_PATH", "/nonexistent/api_keys.json")
    import core.provider_registry as reg

    def boom(messages, **kwargs):
        raise RuntimeError("all providers failed")

    monkeypatch.setattr(reg, "chat_failover", boom)
    agent = dev.NIGHTFALLDevAgent(workspace_dir="/tmp")
    agent.history = [{"role": "user", "content": "hi"}]
    with pytest.raises(RuntimeError) as err:
        agent._call_llm()
    assert "Settings" in str(err.value)
