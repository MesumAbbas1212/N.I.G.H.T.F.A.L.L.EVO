"""
Dynamic Tool Registry & Hot-Plugging Engine
Part of Project Ultron for NIGHTFALL AI.

Manages persistent synthetic skills, dynamic tool declarations for Gemini Live,
hot-reloading, execution dispatch, and lifecycle management.
"""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import inspect
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.user_paths import get_user_data_dir

logger = logging.getLogger("DynamicToolRegistry")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEATURES_DIR = PROJECT_ROOT / "features"
APPDATA_SKILLS_DIR = get_user_data_dir() / "skills"


class DynamicSkill:
    """Represents a loaded, runnable feature or synthetic skill in NIGHTFALL AI."""

    def __init__(self, skill_path: Path, manifest: Dict[str, Any], module: Any = None):
        self.skill_path = skill_path
        self.skill_dir = skill_path if skill_path.is_dir() else skill_path.parent
        self.manifest = manifest
        self.module = module
        self.name: str = manifest.get("name", skill_path.stem)
        self.description: str = manifest.get("description", "")
        self.parameters: Dict[str, Any] = manifest.get("parameters", {"type": "OBJECT", "properties": {}})
        self.triggers: List[str] = manifest.get("triggers", [])
        self.aliases: List[str] = manifest.get("aliases", [])
        self.active: bool = manifest.get("active", True)
        self.version: str = manifest.get("version", "1.0.0")
        self.created_at: float = manifest.get("created_at", time.time())
        self.invocations: int = manifest.get("invocations", 0)
        self.last_error: Optional[str] = manifest.get("last_error", None)

    def _load_module(self) -> Any:
        if self.module is None:
            code_path = self.skill_path / "skill.py" if self.skill_path.is_dir() else self.skill_path
            module_name = f"NIGHTFALL_skill_{self.name}_{abs(hash(str(code_path.resolve())))}"
            spec = importlib.util.spec_from_file_location(module_name, str(code_path))
            if not spec or not spec.loader:
                raise ImportError(f"Unable to load skill module: {code_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            self.module = module
        return self.module

    def to_tool_declaration(self) -> Dict[str, Any]:
        """Returns Gemini function declaration dict."""
        return {
            "name": self.name,
            "description": f"{self.description} (Autonomous dedicated tool: prefer calling this tool directly over generic web search).",
            "parameters": self.parameters,
        }

    def execute_sync(self, **kwargs) -> Any:
        """Executes the skill synchronously (safe for worker threads)."""
        module = self._load_module()
        if not hasattr(module, "execute"):
            raise AttributeError(f"Feature '{self.name}' has no 'execute' function.")
        func = getattr(module, "execute")
        self.invocations += 1
        if inspect.iscoroutinefunction(func):
            return asyncio.run(func(**kwargs))
        return func(**kwargs)

    async def execute_async(self, **kwargs) -> Any:
        """Executes the skill asynchronously without thread-pool registration conflicts."""
        module = self._load_module()
        if not hasattr(module, "execute"):
            raise AttributeError(f"Feature '{self.name}' has no 'execute' function.")

        func = getattr(module, "execute")
        self.invocations += 1

        if inspect.iscoroutinefunction(func):
            return await func(**kwargs)
        else:
            return await asyncio.to_thread(func, **kwargs)


class DynamicToolRegistry:
    """Central registry for all hot-loaded features and synthetic NIGHTFALL skills."""

    _skills: Dict[str, DynamicSkill] = {}
    _initialized: bool = False

    @classmethod
    def get_skills_directory(cls) -> Path:
        FEATURES_DIR.mkdir(parents=True, exist_ok=True)
        return FEATURES_DIR

    @classmethod
    def initialize(cls) -> int:
        """Loads all valid features and skills from the codebase features/ directory and AppData vault."""
        cls._skills.clear()
        FEATURES_DIR.mkdir(parents=True, exist_ok=True)
        count = 0

        # 1. Load native features from codebase features/ directory
        for item in sorted(FEATURES_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
            if item.name.startswith((".", "_")):
                continue

            # Case A: Single Python module file (e.g. features/internet_speed_test.py)
            if item.is_file() and item.suffix == ".py":
                try:
                    source = item.read_text(encoding="utf-8")
                    tree = ast.parse(source, filename=str(item))
                    meta: Dict[str, Any] = {}
                    for node in tree.body:
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
                        if any(isinstance(target, ast.Name) and target.id == "FEATURE_METADATA" for target in targets):
                            value = node.value
                            parsed = ast.literal_eval(value)
                            if isinstance(parsed, dict):
                                meta = parsed
                            break
                    if not meta:
                        meta = {
                            "name": item.stem,
                            "description": ast.get_docstring(tree) or f"Native feature {item.stem}",
                            "parameters": {"type": "OBJECT", "properties": {}},
                        }

                    manifest_path = item.with_suffix("") / "manifest.json"
                    if manifest_path.exists():
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            package_manifest = json.load(f)
                        meta = {**meta, **package_manifest}

                    feat_name = meta.get("name", item.stem)
                    meta.setdefault("created_at", item.stat().st_mtime)
                    skill = DynamicSkill(item, meta)
                    cls._skills[feat_name] = skill
                    for alias in meta.get("aliases", []):
                        if alias not in cls._skills:
                            cls._skills[alias] = skill
                    count += 1
                except Exception as e:
                    logger.warning(f"[Registry] Failed to load codebase feature '{item.name}': {e}")

            # Case B: Feature directory with manifest.json & skill.py
            elif item.is_dir():
                manifest_file = item / "manifest.json"
                code_file = item / "skill.py"
                if manifest_file.exists() and code_file.exists():
                    try:
                        with open(manifest_file, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        skill = DynamicSkill(item, manifest)
                        cls._skills[skill.name] = skill
                        count += 1
                    except Exception as e:
                        logger.warning(f"[Registry] Failed to load feature package '{item.name}': {e}")

        # 2. Check legacy AppData skills vault for backward compatibility
        if APPDATA_SKILLS_DIR.exists():
            for item in APPDATA_SKILLS_DIR.iterdir():
                if item.is_dir() and item.name not in cls._skills:
                    manifest_file = item / "manifest.json"
                    code_file = item / "skill.py"
                    if manifest_file.exists() and code_file.exists():
                        try:
                            with open(manifest_file, "r", encoding="utf-8") as f:
                                manifest = json.load(f)
                            skill = DynamicSkill(item, manifest)
                            cls._skills[skill.name] = skill
                            count += 1
                        except Exception:
                            pass

        cls._initialized = True
        logger.info(f"[Registry] Initialized with {count} features and skills.")
        return count

    @classmethod
    def get_tool_declarations(cls) -> List[Dict[str, Any]]:
        """Returns Gemini-compatible tool declarations for all active skills."""
        if not cls._initialized:
            cls.initialize()
        return [skill.to_tool_declaration() for skill in cls._skills.values() if skill.active]

    @classmethod
    def has_tool(cls, name: str) -> bool:
        if not cls._initialized:
            cls.initialize()
        return name in cls._skills and cls._skills[name].active

    @classmethod
    def get_skill(cls, name: str) -> Optional[DynamicSkill]:
        if not cls._initialized:
            cls.initialize()
        return cls._skills.get(name)

    @classmethod
    def get_latest_skill(cls) -> Optional[DynamicSkill]:
        """Returns the most recently created active synthetic skill."""
        if not cls._initialized:
            cls.initialize()
        active = [s for s in cls._skills.values() if s.active]
        if not active:
            return None
        active.sort(key=lambda s: getattr(s, "created_at", 0), reverse=True)
        return active[0]

    @classmethod
    def find_matching_skill(cls, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Intelligently matches a natural language user command or directive
        to an active synthetic skill in the registry using semantic token matching
        and dynamic metadata-driven parameter extraction. Completely non-hardcoded.
        """
        if not text or not isinstance(text, str):
            return None
        if not cls._initialized:
            cls.initialize()

        q_lower = text.lower().strip()
        q_words = re.findall(r"[a-z0-9]+", q_lower)
        if not q_words:
            return None

        stopwords = {
            "a", "an", "the", "and", "or", "to", "for", "when", "that", "will", "you", "i", "me", "my",
            "on", "in", "at", "by", "from", "of", "with", "about", "is", "are", "was", "were", "be",
            "this", "that", "it", "can", "please", "do", "what", "how", "give", "NIGHTFALL", "tell", "show",
            "run", "use", "start", "execute", "check", "get", "fetch", "try", "call", "launch",
            "skill", "skills", "feature", "features", "tool", "tools", "test", "tests", "testing",
            "made", "make", "using", "u", "ur", "your", "created", "create", "built", "build",
            "forged", "forge", "just", "recently", "newly", "new", "latest", "now", "already",
            "one", "did", "have"
        }

        def stem(w: str) -> str:
            w = w.lower()
            for sfx in ("ing", "ies", "es", "s"):
                if w.endswith(sfx) and len(w) > len(sfx) + 2:
                    return w[:-len(sfx)]
            return w

        q_meaningful = [w for w in q_words if w not in stopwords and len(w) > 2]
        q_stems = {stem(w) for w in q_meaningful}

        # 1. Unnamed skill trigger: ONLY when the user explicitly says 'use the skill', 'test new skill', etc.
        # WITHOUT specifying any specific domain topic (like 'bitcoin', 'cricket', 'internet')!
        generic_directives = {
            "use the skill", "run the skill", "test the skill", "execute the skill",
            "the skill", "use skill", "run skill", "test skill", "use latest skill",
            "run latest skill", "test latest skill", "use new skill", "run new skill",
            "use that skill", "run that skill", "test that skill", "execute that skill",
            "that skill", "this skill", "use this skill", "run this skill", "the feature",
            "use the feature", "run the feature", "launch the skill", "try the skill",
            "use the tool", "run the tool", "test the tool", "execute the tool"
        }
        cleaned_q = " ".join(q_words)
        is_generic_unnamed = (
            cleaned_q in generic_directives or
            (len(q_meaningful) == 0 and any(w in q_words for w in ("skill", "skills", "feature", "features", "tool", "tools"))) or
            (len(q_meaningful) == 0 and any(cleaned_q.startswith(g) for g in ("use", "run", "test", "execute", "start", "launch", "try")))
        )
        if is_generic_unnamed:
            latest = cls.get_latest_skill()
            if latest:
                return latest.name, {}

        # 2. Dynamic metadata-driven semantic skill matching across ALL registered skills
        best_skill = None
        best_score = 0

        for skill in cls._skills.values():
            if not skill.active:
                continue

            score = 0
            s_name_words = re.findall(r"[a-z0-9]+", skill.name.lower())
            s_name_stems = {stem(w) for w in s_name_words if w not in stopwords and len(w) > 2}

            s_desc_words = re.findall(r"[a-z0-9]+", skill.description.lower())
            s_desc_stems = {stem(w) for w in s_desc_words if w not in stopwords and len(w) > 2}

            # Explicit trigger phrases defined by the feature
            for trig in getattr(skill, "triggers", []):
                if trig.lower() in q_lower:
                    score += 200
                    break

            # Explicit aliases defined by the feature
            for alias in getattr(skill, "aliases", []):
                if alias.lower() in q_lower or alias.replace("_", " ").lower() in q_lower:
                    score += 180
                    break

            # Exact skill name match or clean underscore-replaced match
            if skill.name.lower() in q_lower or skill.name.replace("_", " ").lower() in q_lower:
                score += 150

            # Direct phrase match in query (e.g. 'internet speed', 'speed test')
            for phrase_len in (3, 2):
                for i in range(len(q_words) - phrase_len + 1):
                    phrase = " ".join(q_words[i:i + phrase_len])
                    if phrase in skill.name.replace("_", " ").lower() and not all(pw in stopwords for pw in q_words[i:i + phrase_len]):
                        score += 60
                    elif phrase in skill.description.lower() and not all(pw in stopwords for pw in q_words[i:i + phrase_len]):
                        score += 25

            # Skill name stem overlap
            name_hits = s_name_stems.intersection(q_stems)
            score += len(name_hits) * 35
            if s_name_stems and name_hits == s_name_stems:
                score += 50

            # Description stem overlap (only meaningful stems!)
            desc_hits = s_desc_stems.intersection(q_stems)
            score += len(desc_hits) * 6

            # Acronym / token check for meaningful words only
            for word in q_meaningful:
                if len(word) >= 2 and word in skill.name.lower().split("_"):
                    score += 25

            if score > best_score and score >= 35:
                best_score = score
                best_skill = skill

        if not best_skill:
            return None

        # 3. Dynamic parameter extraction from skill manifest
        props = best_skill.parameters.get("properties", {})
        args: Dict[str, Any] = {}
        for p_name, p_spec in props.items():
            p_desc = str(p_spec.get("description", ""))
            p_type = str(p_spec.get("type", "STRING")).upper()

            # Location / Area extraction based on semantic description
            if any(w in p_desc.lower() for w in ("city", "district", "region", "area", "location")):
                for prep in ("around ", "in ", "near ", "over ", "for ", "at "):
                    if prep in q_lower:
                        cand = text[q_lower.find(prep) + len(prep):].strip()
                        cand = re.split(r"[,.?!;]|(?:and\s+)|(?:with\s+)", cand)[0].strip()
                        if cand:
                            args[p_name] = cand
                        break

            # Quoted options / tickers from description e.g. 'AAPL', 'BMW.DE', 'robot', 'floss'
            for opt in re.findall(r"'([a-zA-Z0-9_.-]+)'", p_desc):
                if opt.lower() in q_words:
                    args[p_name] = opt
                    break

            # Label to symbol mapping from description e.g. 'AAPL' for Apple
            examples = re.findall(r"'([A-Za-z0-9_.-]+)'\s+for\s+([A-Za-z0-9]+)", p_desc)
            for code, label in examples:
                if label.lower() in q_words:
                    args[p_name] = code
                    break

            # Number / duration extraction for numeric parameters
            if p_type in ("INTEGER", "NUMBER"):
                m_num = re.search(r"\b(\d+)\s*(?:seconds?|sec|mins?|points?|hours?|kline|bars?|times?)\b", q_lower)
                if m_num:
                    args[p_name] = int(m_num.group(1))

        return best_skill.name, args


    @classmethod
    def list_skills(cls) -> List[Dict[str, Any]]:
        """Returns metadata for all installed skills for UI presentation."""
        if not cls._initialized:
            cls.initialize()
        results = []
        seen = set()
        for s in cls._skills.values():
            if id(s) in seen:
                continue
            seen.add(id(s))
            results.append({
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "active": s.active,
                "created_at": s.created_at,
                "invocations": s.invocations,
                "path": str(s.skill_dir),
                "last_error": s.last_error,
            })
        return results

    @classmethod
    def toggle_skill(cls, name: str, active: Optional[bool] = None) -> bool:
        """Enables or disables a synthetic skill."""
        skill = cls.get_skill(name)
        if not skill:
            return False

        new_status = not skill.active if active is None else bool(active)
        skill.active = new_status

        manifest_path = skill.skill_dir / "manifest.json"
        try:
            skill.manifest["active"] = new_status
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(skill.manifest, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"[Registry] Failed to save toggle status for '{name}': {e}")
            return False

    @classmethod
    def delete_skill(cls, name: str) -> bool:
        """Permanently removes a synthetic skill."""
        skill = cls.get_skill(name)
        if not skill:
            return False

        try:
            if skill.skill_dir.exists():
                shutil.rmtree(skill.skill_dir)
            if name in cls._skills:
                del cls._skills[name]
            return True
        except Exception as e:
            logger.error(f"[Registry] Failed to delete skill '{name}': {e}")
            return False

    @classmethod
    def execute_sync(cls, name: str, args: Dict[str, Any]) -> Any:
        """Dispatches execution synchronously (safe for background worker threads)."""
        skill = cls.get_skill(name)
        if not skill or not skill.active:
            return f"Error: Feature '{name}' is unavailable or deactivated."

        try:
            return skill.execute_sync(**args)
        except Exception as e:
            skill.last_error = str(e)
            logger.error(f"[Registry] Execution error in feature '{name}': {e}", exc_info=True)
            try:
                import traceback
                from actions.auto_heal_engine import AutoHealEngine
                AutoHealEngine.record_last_error(traceback.format_exc())
            except Exception:
                pass
            raise

    @classmethod
    async def execute_async(cls, name: str, args: Dict[str, Any]) -> Any:
        """Dispatches an async execution to a loaded synthetic skill."""
        skill = cls.get_skill(name)
        if not skill or not skill.active:
            return f"Error: Synthetic skill '{name}' is unavailable or deactivated."

        try:
            result = await skill.execute_async(**args)
            return result
        except Exception as e:
            skill.last_error = str(e)
            logger.error(f"[Registry] Execution error in skill '{name}': {e}", exc_info=True)
            # Route to auto heal engine if available
            try:
                import traceback
                from actions.auto_heal_engine import AutoHealEngine
                AutoHealEngine.record_last_error(traceback.format_exc())
            except Exception:
                pass
            raise
