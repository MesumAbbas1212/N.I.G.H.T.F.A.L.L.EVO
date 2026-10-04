"""
Skill Discovery & Autonomous Intent Detector
Part of Project Ultron for NIGHTFALL AI.

Detects capability gaps from voice/text queries, parses explicit "learn this" commands,
and runs the background idle reflection daemon ("Dream Cycle").
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SkillDiscovery")

# Regex triggers for explicit skill creation
LEARN_PATTERNS = [
    # 1. "learn how to...", "teach yourself to...", "figure out how to..."
    r"(?:NIGHTFALL\s*,?\s*)?(?:learn\s+how\s+to|teach\s+yourself\s+(?:how\s+)?to|figure\s+out\s+how\s+to)\s+(.+)",
    # 2. "create/make/build/forge a [optional descriptor] skill/tool/feature that/to/for [action]"
    r"(?:NIGHTFALL\s*,?\s*)?(?:create|forge|build|make|synthesize|add|develop)\s+(?:a\s+)?(?:new\s+)?(?:([\w\s-]{2,30})\s+)?(?:skill|tool|feature|capability|plugin|function)\s+(?:for|to|that|which|allowing|where)\s+(.+)",
    # 3. "create/make/build/forge a [descriptor] skill/tool/feature"
    r"(?:NIGHTFALL\s*,?\s*)?(?:create|forge|build|make|synthesize|add|develop)\s+(?:a\s+)?(?:new\s+)?(.+?)\s+(?:skill|tool|feature|capability|plugin|function)\b",
    # 4. "upgrade yourself with...", "evolve to..."
    r"(?:NIGHTFALL\s*,?\s*)?(?:upgrade\s+yourself\s+with|evolve\s+to)\s+(.+)",
]


class SkillDiscovery:
    """Detects when NIGHTFALL should evolve a new skill."""

    @classmethod
    def analyze_command(cls, text: str) -> Optional[Dict[str, str]]:
        """
        Parses user text to determine if it is an explicit command to learn or forge a skill.
        Returns: {"goal": str, "suggested_name": str} or None
        """
        clean = text.strip()
        for pattern in LEARN_PATTERNS:
            m = re.search(pattern, clean, re.IGNORECASE)
            if m:
                groups = [g.strip() for g in m.groups() if g and g.strip()]
                if len(groups) == 2:
                    descriptor, action = groups
                    goal = f"{descriptor}: {action}"
                    name_source = descriptor
                elif len(groups) == 1:
                    goal = groups[0]
                    name_source = goal
                else:
                    continue

                # Derive a short snake_case name suggestion from name_source
                words = re.findall(r"[a-zA-Z0-9]+", name_source.lower())
                meaningful = [w for w in words if w not in ("a", "an", "the", "to", "for", "in", "of", "and", "how", "that", "it", "will", "allow", "you", "see")][:4]
                suggested_name = "_".join(meaningful) if meaningful else "custom_skill"

                return {
                    "goal": goal,
                    "suggested_name": suggested_name,
                    "raw_command": clean
                }
        return None

    @classmethod
    def execute_forge(cls, goal: str, name: Optional[str] = None) -> Dict[str, Any]:
        """Directly invokes the SkillForge."""
        from core.skill_forge import SkillForge
        return SkillForge.forge_skill(goal=goal, skill_name=name)
