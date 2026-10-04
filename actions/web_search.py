from core.user_paths import get_user_data_dir
#web_search.py
import json
import re
import sys
import time
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_search(query: str) -> str:
    from google import genai

    client   = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config={"tools": [{"google_search": {}}]},
    )

    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text

    text = text.strip()
    if not text:
        raise ValueError("Gemini returned an empty response.")
    return text


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    """DuckDuckGo through the ddgs / duckduckgo_search package."""
    try:
        from ddgs import DDGS
    except ImportError:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=RuntimeWarning,
                message=r"This package .* has been renamed to .*",
            )
            from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


# ---------------------------------------------------------------------------
# Search backends
#
# There used to be exactly one keyless backend (the DuckDuckGo package). When it
# returned nothing - which happens often on a throttled or blocked connection -
# the code fell through to Gemini, and when Gemini was out of quota the fallback
# returned the string "No results found for: <query>". The assistant then told
# the user there was no news in Pakistan, which was simply untrue: the search
# had never run.
#
# Several independent keyless backends are now tried in order of usefulness for
# the query, the reason each one failed is kept, and a search that could not run
# says so instead of pretending the world has no news.
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 15

_NEWS_MARKERS = (
    "news", "headline", "headlines", "breaking", "latest", "today", "yesterday",
    "this week", "this morning", "tonight", "current events", "top stories",
    "happening now", "just happened",
)


def _looks_like_news_query(query: str) -> bool:
    low = (query or "").lower()
    return any(re.search(rf"\b{re.escape(m)}\b", low) for m in _NEWS_MARKERS)


def _clean_ddg_url(url: str) -> str:
    """Unwrap DuckDuckGo's /l/?uddg= redirect links."""
    url = (url or "").strip()
    if "duckduckgo.com/l/" in url or url.startswith("//duckduckgo.com/l/"):
        try:
            qs = parse_qs(urlparse("https:" + url if url.startswith("//") else url).query)
            if qs.get("uddg"):
                return unquote(qs["uddg"][0])
        except Exception:
            pass
    return url


def _google_news_search(query: str, max_results: int = 8) -> list[dict]:
    """Google News RSS: no key, no scraping, and it is what news queries want."""
    import requests

    url = (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    results = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        # Google appends " - Publisher" to the headline; the source is separate.
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].strip()
        # The description is an HTML blob repeating the headline plus a link.
        snippet = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if source and snippet.startswith(title):
            snippet = snippet[len(title):].strip(" -")
        snippet = re.sub(r"\s*" + re.escape(source) + r"\s*$", "", snippet).strip()
        results.append({
            "title": title,
            "snippet": "" if snippet == title else snippet,
            "url": (item.findtext("link") or "").strip(),
            "source": source,
            "published": (item.findtext("pubDate") or "").strip(),
        })
        if len(results) >= max_results:
            break
    return results


def _ddg_html_search(query: str, max_results: int = 6) -> list[dict]:
    """DuckDuckGo's no-JS HTML endpoint, scraped directly."""
    import requests
    from bs4 import BeautifulSoup

    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": _UA},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for block in soup.select(".result"):
        link = block.select_one("a.result__a")
        if link is None:
            continue
        snippet_el = block.select_one(".result__snippet")
        results.append({
            "title": link.get_text(" ", strip=True),
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            "url": _clean_ddg_url(link.get("href", "")),
        })
        if len(results) >= max_results:
            break
    return results


def _bing_search(query: str, max_results: int = 6) -> list[dict]:
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "count": max_results},
        headers={"User-Agent": _UA},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a")
        if link is None:
            continue
        snippet_el = block.select_one("p")
        results.append({
            "title": link.get_text(" ", strip=True),
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            "url": link.get("href", ""),
        })
        if len(results) >= max_results:
            break
    return results


def _wikipedia_search(query: str, max_results: int = 4) -> list[dict]:
    """Last-resort encyclopaedic lookup (never a substitute for news)."""
    import requests

    resp = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": max_results,
        },
        headers={"User-Agent": _UA},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    hits = ((resp.json() or {}).get("query") or {}).get("search") or []

    results = []
    for hit in hits:
        title = str(hit.get("title") or "")
        snippet = re.sub(r"<[^>]+>", "", str(hit.get("snippet") or ""))
        if not title:
            continue
        results.append({
            "title": title,
            "snippet": snippet,
            "url": "https://en.wikipedia.org/wiki/" + quote_plus(title.replace(" ", "_")),
            "source": "Wikipedia",
        })
    return results


def _gemini_as_results(query: str, max_results: int = 1) -> list[dict]:
    """Gemini's grounded answer as a single result (costs quota, so it is last)."""
    text = _gemini_search(query)
    return [{"title": f"Gemini (grounded answer) - {query}", "snippet": text,
             "url": "", "source": "Gemini"}] if text else []


def _search_backends(query: str) -> list:
    backends = []
    if _looks_like_news_query(query):
        # News queries get the news feed first: it is the only backend that is
        # fresh, dated and structured.
        backends.append(("Google News", _google_news_search))
    backends.append(("DuckDuckGo", _ddg_search))
    backends.append(("DuckDuckGo HTML", _ddg_html_search))
    backends.append(("Bing", _bing_search))
    backends.append(("Wikipedia", _wikipedia_search))
    backends.append(("Gemini", _gemini_as_results))
    return backends


#: Repeated searches (the model often asks twice in one turn) reuse a result.
_SEARCH_CACHE: dict = {}
_SEARCH_CACHE_TTL = 60.0


def _search_all(query: str, max_results: int = 6):
    """Try every backend until one answers.

    Returns ``(results, errors)``. ``results`` is empty only after every
    backend was tried, and ``errors`` always explains what happened.
    """
    key = (query or "").strip().lower()
    now = time.time()
    cached = _SEARCH_CACHE.get(key)
    if cached and now - cached[0] < _SEARCH_CACHE_TTL:
        return cached[1], cached[2]

    results: list[dict] = []
    errors: list[str] = []
    for name, backend in _search_backends(query):
        try:
            found = list(backend(query) or [])
        except Exception as exc:
            detail = str(exc)[:140]
            errors.append(f"{name}: {type(exc).__name__}: {detail}")
            print(f"[WebSearch] {name} failed: {detail}")
            continue
        if found:
            print(f"[WebSearch] {name} OK: {len(found)} result(s).")
            results = found[:max_results]
            break
        print(f"[WebSearch] {name} returned nothing.")
        errors.append(f"{name}: no results")

    _SEARCH_CACHE[key] = (now, results, errors)
    return results, errors


def _format_ddg(query: str, results: list[dict]) -> str:
    lines = [f"Search results for: {query}\n"] if results else [f"No results found for: {query}"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_results(query: str, results: list[dict]) -> str:
    lines = [f"Search results for: {query}", ""]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        url = (r.get("url") or "").strip()
        meta = ", ".join(
            part for part in ((r.get("source") or "").strip(),
                              (r.get("published") or "").strip()) if part
        )
        lines.append(f"{i}. {title}" + (f"  [{meta}]" if meta else ""))
        if snippet:
            lines.append(f"   {snippet}")
        if url:
            lines.append(f"   {url}")
        lines.append("")
    lines.append(
        f"({len(results)} source(s), searched {time.strftime('%Y-%m-%d %H:%M')}. "
        "Quote the dates and sources when you answer.)"
    )
    return "\n".join(lines).strip()


def _format_search_failure(query: str, errors: list[str]) -> str:
    lines = [f"SEARCH UNAVAILABLE for: {query}", ""]
    lines.append("Every search backend was tried and none of them answered:")
    lines.extend(f"- {e}" for e in errors)
    lines.append("")
    lines.append(
        "Tell the user you could not reach the search service right now. "
        "Do NOT say there is no news or no such information - the search never ran."
    )
    return "\n".join(lines)


def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    try:
        return _gemini_search(query)
    except Exception as e:
        print(f"[WebSearch] Gemini compare failed: {e} - falling back to web results")

    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
            if not all_results[item]:
                all_results[item] = _ddg_html_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison - {aspect.upper()}", "-" * 40]
    for item in items:
        lines.append(f"\n> {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  * {r['snippet']}")
    return "\n".join(lines)


def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = (params.get("query") or "").strip()
    mode   = (params.get("mode") or "search").lower().strip()
    items  = params.get("items", [])
    aspect = (params.get("aspect") or "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] Query: {query!r}  Mode: {mode}")
    if mode == "compare":
        try:
            return _compare(items or ([query] if query else []), aspect)
        except Exception as e:
            return f"Comparison failed: {e}"

    results, errors = _search_all(query)
    if not results:
        # Say which of the two it was: a search that never ran, or a search
        # that ran and genuinely found nothing.
        if any(not e.endswith("no results") for e in errors):
            print(f"[WebSearch] every backend failed: {errors}")
            return _format_search_failure(query, errors)
        return _format_ddg(query, [])

    if player and hasattr(player, "show_hud_operation"):
        try:
            sources = [r.get("url") or r.get("title") for r in results[:4]
                       if r.get("url") or r.get("title")]
            player.show_hud_operation(
                "WEB INTELLIGENCE",
                f"Retrieved {len(results)} sources for '{query[:30]}'",
                sources=sources, tool="SEARCH",
            )
        except Exception:
            pass
    if player and hasattr(player, "show_hud_deliverable"):
        try:
            bullets = [r.get("title") for r in results[:4] if r.get("title")]
            player.show_hud_deliverable(
                f"SEARCH: {query[:25].upper()}", bullets=bullets, kind="search"
            )
        except Exception:
            pass
    return _format_results(query, results)
