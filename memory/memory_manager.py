from __future__ import annotations
from core.user_paths import get_user_data_dir

import json
import re
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys

for _s_name in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _s_name, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = get_user_data_dir() / "memory" / "long_term.json"
_lock            = Lock()
MAX_VALUE_LENGTH = 380

# ── Why there are two very different numbers here ────────────────────────────
#
# There used to be one: MEMORY_MAX_CHARS = 2200, applied to the whole store. It
# was a *storage* limit, and it existed only because the entire memory was
# pasted into the system prompt on every connect — so growing the memory grew
# every single request. When it filled, _trim_to_limit() deleted the oldest
# entries and printed one line to a console nobody reads. A memory described as
# "deeply remembers projects, preferences and personal context" was in practice
# two pages long, and quietly forgot your sister's name after a few weeks.
#
# Storage and prompt budget are now separate concerns:
#
#   MEMORY_MAX_CHARS  — a runaway guard, not a feature limit. Nothing normal
#                       reaches it; a bug writing in a loop does.
#   PROMPT_CORE_CHARS — what actually rides in the system prompt every session.
#                       Smaller than the old whole-memory dump, so sessions
#                       start *faster* than before, not slower.
#
# Everything above the core stays on disk and is fetched on demand by the
# recall_memory tool — see search_memory() and format_memory_for_prompt().
MEMORY_MAX_CHARS  = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
# Most entries any one category may contribute to the core block, so a person
# with forty stored preferences still gets their sister into the prompt.
PROMPT_MAX_PER_CATEGORY = 6

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as e:
            print(f"[Memory] ⚠️ Load error: {e}")
            return _empty_memory()

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


# Set by main.py so a trim can reach the activity log. Deleting something a
# person told you and mentioning it only on stdout is how a memory loses trust.
_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    dropped = []
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        dropped.append(f"{cat}/{key}")
        print(f"[Memory] 🗑️  Trimmed {cat}/{key}")
    if dropped and _trim_notifier:
        try:
            _trim_notifier(
                f"SYS: Memory full — forgot {len(dropped)} oldest entries "
                f"({', '.join(dropped[:3])}{'…' if len(dropped) > 3 else ''})"
            )
        except Exception:
            pass
    return memory

def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
    return memory


def should_extract_memory(user_text: str, NIGHTFALL_text: str, api_key: str = "") -> bool:
    try:
        from or_client import client

        combined = f"User: {user_text[:300]}\nNIGHTFALL AI: {NIGHTFALL_text[:1000]}"

        result = client.chat(
            f"Does this conversation contain ANY of the following?\n"
            f"- Personal facts (name, age, city, job, birthday, nationality)\n"
            f"- Preferences or favorites (food, color, music, sport, game, film, book, etc.)\n"
            f"- Active projects or goals the user is working on\n"
            f"- People in the user's life (friends, family, partner, colleagues)\n"
            f"- Things the user wants to do or buy in the future\n"
            f"- Any other fact worth remembering long-term\n\n"
            f"Reply only YES or NO.\n\nConversation:\n{combined}",
            system="You are a memory relevance checker. Reply only YES or NO.",
            max_tokens=5,
            temperature=0.0,
        )
        return "YES" in result.upper()

    except Exception as e:
        print(f"[Memory] ⚠️ Stage1 check failed: {e}")
        return False


def extract_memory(user_text: str, NIGHTFALL_text: str, api_key: str = "") -> dict:
    try:
        from or_client import client

        combined = f"User: {user_text[:600]}\nNIGHTFALL AI: {NIGHTFALL_text[:300]}"

        raw = client.chat(
            f"Extract ALL memorable personal facts from this conversation. Any language.\n"
            f"Return ONLY valid JSON. Use {{}} if truly nothing is worth saving.\n\n"
            f"Category guide:\n"
            f"  identity      → name, age, birthday, city, country, job, school, nationality, language\n"
            f"  preferences   → ANY favorite or preferred thing:\n"
            f"                  favorite_food, favorite_color, favorite_music, favorite_film,\n"
            f"                  favorite_game, favorite_sport, favorite_book, favorite_artist,\n"
            f"                  favorite_country, hobbies, interests, dislikes, etc.\n"
            f"  projects      → projects being built, ongoing work, goals, ideas in progress\n"
            f"                  (e.g. NIGHTFALL_ai: 'Building a NIGHTFALL AI - Lite assistant')\n"
            f"  relationships → people mentioned: friends, family, partner, colleagues\n"
            f"                  (e.g. best_friend_alex: 'Best friend, met in university')\n"
            f"  wishes        → future plans, things to buy, travel plans, dreams\n"
            f"  notes         → anything else worth remembering (habits, schedule, etc.)\n\n"
            f"IMPORTANT:\n"
            f"- Be LIBERAL: if something MIGHT be worth remembering, include it.\n"
            f"- Extract from BOTH user and NIGHTFALL AI turns.\n"
            f"- Skip: weather, reminders, search results, one-time commands.\n"
            f"- Use concise English values regardless of conversation language.\n\n"
            f"Format:\n"
            f'{{"identity":{{"name":{{"value":"User"}}}},\n'
            f' "preferences":{{"favorite_color":{{"value":"blue"}}}},\n'
            f' "projects":{{"NIGHTFALL_ai":{{"value":"NIGHTFALL AI - Lite assistant"}}}},\n'
            f' "relationships":{{"friend_alex":{{"value":"close friend"}}}},\n'
            f' "wishes":{{"buy_guitar":{{"value":"wants an acoustic guitar"}}}},\n'
            f' "notes":{{"works_at_night":{{"value":"usually active late at night"}}}}}}\n\n'
            f"Conversation:\n{combined}\n\nJSON:",
            system="Return ONLY valid JSON. No markdown, no explanation, no extra text.",
            max_tokens=1024,
            temperature=0.2,
        )

        clean = raw.strip()
        clean = re.sub(r"```(?:json)?", "", clean).strip().rstrip("`").strip()

        if not clean or clean == "{}":
            return {}

        return json.loads(clean)

    except json.JSONDecodeError:
        return {}
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ Extract failed: {e}")
        return {}


def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string,
    because early versions of the store wrote plain strings."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


# Identity is always in the prompt; these categories compete for the remaining
# budget by recency.
_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt.

    This used to dump everything. It now sends three things:

      1. IDENTITY  - always, in full. It is small, and it is wrong for the
         assistant to have to look up your name.
      2. RECENT    - the most recently updated entries from every other
         category, up to PROMPT_CORE_CHARS. Recency is the cheapest useful
         relevance signal available without embeddings.
      3. AN INDEX  - the *keys* of everything else, values omitted.

    Point 3 is what makes recall work at all. A model cannot decide to look
    something up if it does not know the thing exists: with only points 1 and 2,
    "who is Ayse?" would get "I don't know" while ayse_sister sat on disk
    unread. The index costs a few hundred characters and turns recall from a
    gamble into a lookup.

    Net effect on latency: this block is SMALLER than the old full dump, so
    every session connects with fewer tokens. Occasionally the model spends one
    extra round trip on recall_memory - covered by the acknowledgment it
    already speaks before any slow step."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            # Labelled as an observation, not a setting. A bare "Language:
            # English" line written months ago reads like a standing order and
            # was one of the reasons a Turkish question came back in English.
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)")
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    # Recency decides order, but no single category may take the whole budget.
    # Without the cap, someone with forty stored preferences gets a prompt that
    # is forty preferences and not one person's name — the categories that
    # matter most in conversation are also the ones that change least often, so
    # pure recency systematically buries them.
    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        line = f"  - {_pretty(key).title()}: {val}"
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    # The index is a table of contents, so it is interleaved across categories
    # rather than continuing in recency order. Sorted by recency it would list
    # twenty-four preferences before the first relationship, and the one entry
    # the index exists for — the old fact the model has no other way to know
    # about — would fall off the end.
    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    if not core_lines and not indexed:
        return ""

    out = [
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
        *core_lines,
    ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    """Cheap lexical relevance. No embeddings, no network, no model call - this
    runs in well under a millisecond, which is the entire point: recall must
    cost one model round trip, never two."""
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    score   = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key:
            score += 10
        elif w in hay_key:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query`. Backs the recall_memory tool.

    An empty query is treated as "show me everything you know", capped - the
    model asks that when the user says "what do you remember about me?"."""
    memory = load_memory()
    words  = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]

    rows: list[tuple[int, str, str, str]] = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue                     # skip 'sessions', which is a list
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            s = _score(words, cat, key, val) if words else 1
            if s > 0:
                rows.append((s, cat, key, val))

    if not rows:
        return (f"Nothing stored about '{query}'." if query
                else "I have not stored anything about this person yet.")

    rows.sort(key=lambda r: (-r[0], r[2]))
    lines = [f"{cat}/{_pretty(key)}: {val}" for _s, cat, key, val in rows[:max(1, limit)]]
    head  = (f"Stored facts matching '{query}':" if query
             else "Everything currently stored:")
    more  = (f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
             if len(rows) > len(lines) else "")
    return head + "\n" + "\n".join(lines) + more


def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel: what NIGHTFALL knows, and when it learned it.
    Sorted newest first so the panel opens on what changed most recently."""
    memory = load_memory()
    rows = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            rows.append({
                "category": cat,
                "key":      key,
                "value":    val,
                "updated":  (entry.get("updated", "") if isinstance(entry, dict) else ""),
            })
    rows.sort(key=lambda r: (r["updated"] or "0000-00-00"), reverse=True)
    return rows

def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget


# ── Session memory ─────────────────────────────────────────────────────────────

_SESSION_MAX = 3   # safety cap — in practice 0-1 entries after pop


def save_session_summary(summary: str, language: str = "") -> None:
    """Append a 1-2 sentence session summary to long_term.json['sessions']."""
    summary = (summary or "").strip()
    if not summary:
        return
    memory   = load_memory()
    sessions = memory.get("sessions", [])
    if not isinstance(sessions, list):
        sessions = []
    entry: dict = {
        "date":    datetime.now().strftime("%Y-%m-%d"),
        "summary": summary[:280],
    }
    if language:
        entry["language"] = language
    sessions.append(entry)
    memory["sessions"] = sessions[-_SESSION_MAX:]
    with _lock:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    print(f"[Memory] 📝 Session saved ({entry['date']}): {summary[:60]}…")


def pop_last_session() -> dict | None:
    """
    Return AND remove the most recent session entry.
    Calling this consumes the entry so it is never repeated in future briefings.
    """
    with _lock:
        if not MEMORY_PATH.exists():
            return None
        try:
            memory   = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            sessions = memory.get("sessions", [])
            if not isinstance(sessions, list) or not sessions:
                return None
            entry = sessions.pop()          # remove the last entry
            memory["sessions"] = sessions
            MEMORY_PATH.write_text(
                json.dumps(memory, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return entry
        except Exception as e:
            print(f"[Memory] ⚠️ pop_last_session error: {e}")
            return None

# ── Chat History ──────────────────────────────────────────────────────────────
CHAT_HISTORY_PATH = get_user_data_dir() / "memory" / "chat_history.json"
MAX_HISTORY_LENGTH = 40

def load_chat_history() -> list[dict]:
    if not CHAT_HISTORY_PATH.exists():
        return []
    with _lock:
        try:
            data = json.loads(CHAT_HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception as e:
            print(f"[Memory] ⚠️ Chat history load error: {e}")
        return []

def save_chat_history(history: list[dict]) -> None:
    CHAT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        CHAT_HISTORY_PATH.write_text(
            json.dumps(history[-MAX_HISTORY_LENGTH:], indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

def append_to_chat_history(user_msg: str, ai_reply: str) -> None:
    history = load_chat_history()
    if user_msg:
        history.append({"role": "user", "content": user_msg})
    if ai_reply:
        history.append({"role": "assistant", "content": ai_reply})
    save_chat_history(history)


# ── Living Knowledge Graph & Auto-Learning (Pillar 5) ─────────────────────────

_COMMON_TECH = {
    "python": "Python",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "react": "React",
    "vue": "Vue.js",
    "angular": "Angular",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "flutter": "Flutter",
    "dart": "Dart",
    "rust": "Rust",
    "golang": "Go",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "tailwind": "Tailwind CSS",
    "postgres": "PostgreSQL",
    "sqlite": "SQLite",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "next.js": "Next.js",
    "nextjs": "Next.js",
}


def auto_learn_interaction(user_text: str, NIGHTFALL_text: str = "") -> dict:
    """
    Adaptive Living Knowledge Graph extractor (Pillar 5).
    Runs deterministic, zero-latency heuristic extraction across user interaction turns:
    - User name / identity facts ('my name is ...', 'call me ...')
    - City / location ('live in ...', 'based in ...')
    - Email addresses
    - Project workspaces and absolute directory paths
    - Projects being developed ('working on ... project/app', 'building ...')
    - Tech stack, tools, and frameworks (Python, React, FastAPI, Docker, etc.)
    - Explicit user preferences ('i prefer ...', 'my favorite ...')

    Persists newly discovered facts directly into memory/long_term.json.
    Returns the dictionary of updates made.
    """
    if not user_text or len(user_text.strip()) < 4:
        return {}

    text = user_text.strip()
    low_u = text.lower()
    updates: dict[str, dict] = {}

    # 1. Identity: Name
    m_name = re.search(r"\b(?:my name is|call me|i am called)\s+([A-Z][a-zA-Z]{1,20})\b", text)
    if m_name:
        name_val = m_name.group(1).capitalize()
        if name_val.lower() not in ("NIGHTFALL", "sir", "user", "admin", "echo", "assistant", "here"):
            updates.setdefault("identity", {})["name"] = {"value": name_val}

    # 2. Identity: City / Location
    m_city = re.search(r"\b(?:i live in|i'm living in|i'm based in|based in|located in)\s+([A-Za-z\s]{2,25})\b", text, re.IGNORECASE)
    if m_city:
        city_cand = m_city.group(1).strip()
        if city_cand.lower() not in ("india", "scratch", "now", "home", "here", "today", "yesterday"):
            updates.setdefault("identity", {})["city"] = {"value": city_cand.title()}

    # 3. Identity: Email
    m_email = re.search(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b", text)
    if m_email:
        updates.setdefault("identity", {})["email"] = {"value": m_email.group(1).lower()}

    # 4. Projects: Absolute workspace directories
    paths = re.findall(r"\b([A-Za-z]:\\[^<>\":|?*\n\r]+?)(?=[,\s;'\"]|$)", text)
    for p in paths:
        clean_p = p.strip().rstrip("\\.,;")
        if len(clean_p) > 3 and ("\\" in clean_p):
            folder_name = Path(clean_p).name or "workspace"
            slug = re.sub(r"[^a-z0-9_]+", "_", folder_name.lower()).strip("_")
            if slug:
                updates.setdefault("projects", {})[f"path_{slug}"] = {
                    "value": f"Workspace directory: {clean_p}"
                }

    # 5. Projects: Active development
    m_proj = re.search(
        r"\b(?:working on|building|creating|developing)\s+(?:a|an|the|my)?\s*([a-zA-Z0-9_\-\s]{2,30}?)\s*(?:app|application|project|tool|bot|service|website|backend|frontend)\b",
        text,
        re.IGNORECASE
    )
    if m_proj:
        proj_name = m_proj.group(1).strip()
        slug = re.sub(r"[^a-z0-9_]+", "_", proj_name.lower()).strip("_")
        if slug and len(slug) > 2 and slug not in ("new", "this", "that", "some", "a"):
            updates.setdefault("projects", {})[slug] = {
                "value": f"Developing {proj_name} project"
            }

    # 6. Tech Stacks & Frameworks
    if any(kw in low_u for kw in ("stack", "use ", "using", "built with", "coded in", "prefer ", "framework", "language")):
        detected_tech = []
        for tech_kw, tech_name in _COMMON_TECH.items():
            if re.search(rf"\b{re.escape(tech_kw)}\b", low_u):
                detected_tech.append(tech_name)
        if detected_tech:
            updates.setdefault("preferences", {})["tech_stack"] = {
                "value": f"Tech stack: {', '.join(detected_tech)}"
            }

    # 7. Explicit Preferences
    m_pref = re.search(r"\b(?:i prefer|i like using|my preferred)\s+([a-zA-Z0-9_\s]{2,30})\b", text, re.IGNORECASE)
    if m_pref:
        pref_val = m_pref.group(1).strip()
        slug = re.sub(r"[^a-z0-9_]+", "_", pref_val.lower()).strip("_")
        if slug and len(slug) > 2 and slug not in ("to", "a", "the", "that"):
            updates.setdefault("preferences", {})[slug] = {"value": f"Prefers {pref_val}"}

    if updates:
        update_memory(updates)

    return updates

