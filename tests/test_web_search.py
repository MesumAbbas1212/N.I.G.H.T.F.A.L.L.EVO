"""Search must report honestly and try more than one backend.

Reported behaviour: "give me a report on today's critical news in Pakistan"
answered "No results found for: critical news Pakistan October 4 2026 march",
and the assistant then told the user there was no such news. The search had
never run: the single keyless backend (the DuckDuckGo package) returned nothing
and the Gemini fallback was out of quota, and the failed path returned the
string "No results found for: <query>".

These tests pin: several independent backends, news queries going to the news
feed first, and a failed search saying so instead of pretending the world is
empty.
"""

import importlib
import sys
import types

import pytest

from actions import web_search as ws


# -- query classification ----------------------------------------------------

@pytest.mark.parametrize("query", [
    "give me a report on todays critical news in pakistan",
    "what is the latest on the march",
    "top news pakistan today",
    "breaking news",
    "what happened today in islamabad",
])
def test_news_queries_are_recognised(query):
    assert ws._looks_like_news_query(query) is True


@pytest.mark.parametrize("query", [
    "capital of australia",
    "how tall is mount everest",
    "python list comprehension syntax",
])
def test_plain_queries_are_not_news(query):
    assert ws._looks_like_news_query(query) is False


def test_news_queries_use_the_news_feed_first():
    names = [name for name, _ in ws._search_backends("top news pakistan today")]
    assert names[0] == "Google News"
    assert "DuckDuckGo" in names and "Bing" in names


def test_plain_queries_skip_the_news_feed():
    names = [name for name, _ in ws._search_backends("capital of australia")]
    assert "Google News" not in names
    assert names[0] == "DuckDuckGo"


# -- backend fallbacks -------------------------------------------------------

def test_search_falls_through_to_the_next_backend(monkeypatch):
    calls = []

    def dead(query, max_results=6):
        calls.append("ddg")
        return []

    def alive(query, max_results=6):
        calls.append("html")
        return [{"title": "Headline", "snippet": "body", "url": "http://x"}]

    monkeypatch.setattr(ws, "_ddg_search", dead)
    monkeypatch.setattr(ws, "_ddg_html_search", alive)
    ws._SEARCH_CACHE.clear()

    results, errors = ws._search_all("capital of australia")
    assert [r["title"] for r in results] == ["Headline"]
    assert calls == ["ddg", "html"]
    assert any("no results" in e for e in errors)


def test_a_news_query_stops_at_the_news_feed(monkeypatch):
    calls = []

    def news(query, max_results=8):
        calls.append("news")
        return [{"title": "March begins", "snippet": "s", "url": "u",
                 "source": "BBC", "published": "Sun, 04 Oct 2026 12:11:11 GMT"}]

    monkeypatch.setattr(ws, "_google_news_search", news)
    monkeypatch.setattr(ws, "_ddg_search", lambda *a, **k: calls.append("ddg") or [])
    ws._SEARCH_CACHE.clear()

    results, errors = ws._search_all("top news pakistan today")
    assert results and results[0]["title"] == "March begins"
    assert calls == ["news"]


def test_a_backend_that_raises_is_not_fatal(monkeypatch):
    def boom(query, max_results=6):
        raise RuntimeError("TLS handshake failed")

    monkeypatch.setattr(ws, "_ddg_search", boom)
    monkeypatch.setattr(ws, "_google_news_search", lambda q, max_results=8: [])
    monkeypatch.setattr(
        ws, "_ddg_html_search",
        lambda q, max_results=6: [{"title": "T", "snippet": "s", "url": "u"}],
    )
    ws._SEARCH_CACHE.clear()

    results, errors = ws._search_all("top news pakistan today")
    assert results and results[0]["title"] == "T"
    assert any("TLS handshake failed" in e for e in errors)


def test_a_search_that_never_ran_says_so(monkeypatch):
    """The whole point: never claim there is no news when nothing answered."""
    def boom(query, **kwargs):
        raise OSError("network unreachable")

    for name in ("_google_news_search", "_ddg_search", "_ddg_html_search",
                 "_bing_search", "_wikipedia_search", "_gemini_as_results"):
        monkeypatch.setattr(ws, name, boom)
    ws._SEARCH_CACHE.clear()

    output = ws.web_search({"query": "critical news pakistan today"})
    assert "SEARCH UNAVAILABLE" in output
    assert "Do NOT say there is no news" in output
    assert "network unreachable" in output
    assert "No results found" not in output


def test_a_run_that_found_nothing_says_that_instead(monkeypatch):
    monkeypatch.setattr(ws, "_google_news_search", lambda q, max_results=8: [])
    monkeypatch.setattr(ws, "_ddg_search", lambda q, max_results=6: [])
    monkeypatch.setattr(ws, "_ddg_html_search", lambda q, max_results=6: [])
    monkeypatch.setattr(ws, "_bing_search", lambda q, max_results=6: [])
    monkeypatch.setattr(ws, "_wikipedia_search", lambda q, max_results=4: [])
    monkeypatch.setattr(ws, "_gemini_as_results", lambda q, max_results=1: [])
    ws._SEARCH_CACHE.clear()

    output = ws.web_search({"query": "asdkjhasd kjhasd"})
    assert "No results found" in output
    assert "SEARCH UNAVAILABLE" not in output


def test_results_are_cached_briefly(monkeypatch):
    calls = []

    def counted(query, max_results=6):
        calls.append(query)
        return [{"title": "T", "snippet": "", "url": ""}]

    monkeypatch.setattr(ws, "_ddg_search", counted)
    ws._SEARCH_CACHE.clear()

    ws.web_search({"query": "same query twice"})
    ws.web_search({"query": "same query twice"})
    assert calls == ["same query twice"]


# -- Google News RSS parsing (offline fixture) -------------------------------

NEWS_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>Imran Khan supporters to begin march towards Islamabad today - Pajhwok Afghan News</title>
  <link>https://news.google.com/rss/articles/abc</link>
  <pubDate>Sun, 04 Oct 2026 12:11:11 GMT</pubDate>
  <source url="https://pajhwok.com">Pajhwok Afghan News</source>
  <description>&lt;a href="https://x"&gt;Imran Khan supporters to begin march towards Islamabad today&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font&gt;Pajhwok Afghan News&lt;/font&gt;</description>
</item>
</channel></rss>"""


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def test_google_news_rss_is_parsed(monkeypatch):
    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda *a, **k: _FakeResponse(NEWS_RSS.encode("utf-8"))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    results = ws._google_news_search("Pakistan news today")

    assert len(results) == 1
    item = results[0]
    assert item["title"] == "Imran Khan supporters to begin march towards Islamabad today"
    assert item["source"] == "Pajhwok Afghan News"
    assert "12:11:11" in item["published"]
    assert item["url"] == "https://news.google.com/rss/articles/abc"


def test_formatted_results_carry_dates_and_sources():
    text = ws._format_results("pakistan news", [{
        "title": "March begins", "snippet": "Hundreds gather", "url": "http://x",
        "source": "BBC", "published": "Sun, 04 Oct 2026 12:11:11 GMT",
    }])
    assert "March begins" in text
    assert "BBC" in text and "04 Oct 2026" in text
    assert "Hundreds gather" in text


def test_ddg_redirect_links_are_unwrapped():
    assert ws._clean_ddg_url(
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.bbc.com%2Fnews%2F1&rut=x"
    ) == "https://www.bbc.com/news/1"


def test_importing_the_search_module_stays_light():
    """main.py imports this at startup, so no HTTP stack at import time."""
    import subprocess

    code = (
        "import sys; sys.path.insert(0, '.');"
        "import actions.web_search as w;"
        "print('requests' in sys.modules, 'bs4' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd="."
    )
    assert out.stdout.strip() == "False False", out.stderr[-400:]
