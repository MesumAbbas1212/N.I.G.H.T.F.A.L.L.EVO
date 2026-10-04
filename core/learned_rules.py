"""
Learned Rules Engine for NIGHTFALL AI
Enables continuous self-improvement by capturing user corrections, preferred behaviors,
and habits, then injecting them dynamically into the core LLM prompt without modifying source code.
"""

from __future__ import annotations
from core.user_paths import get_user_data_dir

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("LearnedRules")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = get_user_data_dir() / "config"
RULES_FILE = CONFIG_DIR / "learned_rules.json"


class LearnedRulesEngine:
    """Manages persistent behavioral rules and directives learned from the user."""

    @staticmethod
    def _load_raw() -> List[Dict[str, Any]]:
        if not RULES_FILE.exists():
            return []
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"[LearnedRules] Failed to load rules: {e}")
            return []

    @staticmethod
    def _save_raw(rules: List[Dict[str, Any]]) -> bool:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"[LearnedRules] Failed to save rules: {e}")
            return False

    @classmethod
    def add_rule(cls, rule_text: str, category: str = "general", origin: str = "user_correction") -> Dict[str, Any]:
        """Adds a new learned rule to the database."""
        clean_text = rule_text.strip()
        if not clean_text:
            return {"success": False, "message": "Rule text cannot be empty."}

        rules = cls._load_raw()

        # Deduplicate
        for r in rules:
            if r.get("rule", "").lower() == clean_text.lower():
                r["active"] = True
                r["updated_at"] = time.time()
                cls._save_raw(rules)
                return {"success": True, "rule": r, "message": "Rule already exists and is active."}

        new_rule = {
            "id": str(uuid.uuid4())[:8],
            "rule": clean_text,
            "category": category,
            "origin": origin,
            "active": True,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        rules.append(new_rule)
        cls._save_raw(rules)
        return {"success": True, "rule": new_rule, "message": f"Learned new rule: '{clean_text}'"}

    @classmethod
    def list_rules(cls, active_only: bool = False) -> List[Dict[str, Any]]:
        """Returns all stored rules."""
        rules = cls._load_raw()
        if active_only:
            return [r for r in rules if r.get("active", True)]
        return rules

    @classmethod
    def toggle_rule(cls, rule_id: str) -> Dict[str, Any]:
        """Toggles a rule on or off."""
        rules = cls._load_raw()
        for r in rules:
            if r.get("id") == rule_id:
                r["active"] = not r.get("active", True)
                r["updated_at"] = time.time()
                cls._save_raw(rules)
                status = "activated" if r["active"] else "deactivated"
                return {"success": True, "message": f"Rule {rule_id} {status}."}
        return {"success": False, "message": f"Rule ID '{rule_id}' not found."}

    @classmethod
    def delete_rule(cls, rule_id: str) -> Dict[str, Any]:
        """Deletes a rule permanently."""
        rules = cls._load_raw()
        initial_len = len(rules)
        rules = [r for r in rules if r.get("id") != rule_id]
        if len(rules) < initial_len:
            cls._save_raw(rules)
            return {"success": True, "message": f"Rule {rule_id} deleted."}
        return {"success": False, "message": f"Rule ID '{rule_id}' not found."}

    @classmethod
    def get_prompt_injections(cls) -> str:
        """Formats all active rules for runtime system prompt injection."""
        active = [r.get("rule", "").strip() for r in cls.list_rules(active_only=True) if r.get("rule")]
        if not active:
            return ""

        lines = ["\nUSER PREFERENCES & LEARNED BEHAVIORAL DIRECTIVES:"]
        for i, rule in enumerate(active, 1):
            lines.append(f"{i}. {rule}")
        return "\n".join(lines) + "\n"
