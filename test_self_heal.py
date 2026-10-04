"""
NIGHTFALL AI - Autonomous Self-Healing & Self-Improvement Test Runner
Demonstrates:
1. Catching an unhandled runtime exception in an action (ZeroDivisionError)
2. Autonomous analysis, AST sandbox verification, and atomic self-patching
3. Re-executing the patched action to confirm successful hotfix
4. Adding and inspecting a learned behavioral rule (Continuous Self-Improvement)
5. Safe rollback to keep test_action ready for future tests
"""

import sys
import time
import traceback
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from actions.auto_heal_engine import AutoHealEngine, SafetySandbox, auto_heal
from actions.test_action import test_action
from core.learned_rules import LearnedRulesEngine


def run_test():
    print("=" * 65)
    print(" [NIGHTFALL AI] SELF-HEALING & SELF-IMPROVEMENT TEST SUITE")
    print("=" * 65)

    # -------------------------------------------------------------
    # STEP 1: TRIGGER DELIBERATE BUG
    # -------------------------------------------------------------
    print("\n[STEP 1] Executing test_action.py (expecting ZeroDivisionError)...")
    try:
        test_action({})
        print("[INFO] test_action succeeded without error. It might already be patched.")
    except Exception as exc:
        tb_str = traceback.format_exc()
        AutoHealEngine.record_last_error(tb_str)
        print(f"[OK] Captured expected exception: {type(exc).__name__}: {exc}")
        print("[OK] Traceback registered with AutoHealEngine.")

    # -------------------------------------------------------------
    # STEP 2: AUTONOMOUS HEALING & AST VERIFICATION
    # -------------------------------------------------------------
    print("\n[STEP 2] Triggering Auto-Heal Engine (LLM synthesis + AST sandbox)...")
    print("Analyzing traceback and synthesizing surgical patch...")
    start_time = time.time()
    heal_result = auto_heal({"action": "heal"})
    elapsed = time.time() - start_time
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Result:\n{heal_result}")

    # -------------------------------------------------------------
    # STEP 3: RE-VERIFY TEST_ACTION
    # -------------------------------------------------------------
    print("\n[STEP 3] Re-running test_action.py to verify hotfix...")
    try:
        import importlib
        import actions.test_action
        importlib.reload(actions.test_action)
        
        retest_msg = actions.test_action.test_action({})
        print(f"[SUCCESS] Patched code executed cleanly:")
        print(f"   Response: {retest_msg}")
    except Exception as exc:
        print(f"[FAIL] Failed to run patched code: {exc}")

    # -------------------------------------------------------------
    # STEP 4: CONTINUOUS SELF-IMPROVEMENT (LEARNED RULES)
    # -------------------------------------------------------------
    print("\n[STEP 4] Testing Continuous Self-Improvement (Learned Rules)...")
    sample_rule = "Always format system diagnostic reports in bullet points"
    learn_res = LearnedRulesEngine.add_rule(sample_rule, origin="headless_test_suite")
    print(f"Rule Teaching Result: {learn_res.get('message')}")
    
    active_rules = LearnedRulesEngine.list_rules(active_only=True)
    print(f"Total Active Learned Directives in Memory: {len(active_rules)}")
    for r in active_rules[-3:]:
        print(f"   * {r.get('rule')}")

    # -------------------------------------------------------------
    # STEP 5: ROLLBACK (CLEANUP)
    # -------------------------------------------------------------
    print("\n[STEP 5] Testing Safe Rollback...")
    rollback_res = auto_heal({"action": "rollback", "patch_id": "latest"})
    print(f"Rollback result: {rollback_res}")
    
    import importlib
    import actions.test_action
    importlib.reload(actions.test_action)
    print("Reset test_action.py back to original state for future tests.")
    print("=" * 65)
    print("ALL SELF-HEALING & SELF-IMPROVEMENT CHECKS COMPLETED!")
    print("=" * 65)


if __name__ == "__main__":
    run_test()
