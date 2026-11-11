import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from decision_engine import decide  # noqa: E402

HI = dict(session_min=120, scroll_per_min=80, hour=2, streak_days=7, user_limit_min=30,
          category="social", current_action="FRICTION", mins_since_last_intervention=60,
          recent_overrides=0)
SB = {**HI, "softblock_enabled": True, "softblocks_today": 0}


def act(s, ok=False):
    return decide(s, ok)["action"]


class Guardrails(unittest.TestCase):
    def test_essential_apps_always_allow(self):
        for cat in ["calls", "Messaging ", " PAYMENTS", "alarms", "maps", "banking", "emergency"]:
            self.assertEqual(act({**SB, "category": cat, "current_action": "SOFT_BLOCK"}, True), "ALLOW")

    def test_softblock_needs_strict_boolean_consent(self):
        self.assertEqual(act(SB, True), "SOFT_BLOCK")
        self.assertEqual(act(SB, False), "FRICTION")
        for bad in [False, "false", "no", "true", 1, None]:
            self.assertEqual(act({**SB, "softblock_enabled": bad}, True), "FRICTION", bad)

    def test_bad_daily_counts_rejected(self):
        for bad in [-1, True, False, 2, 5, "0", 1.0, None]:
            self.assertEqual(act({**SB, "softblocks_today": bad}, True), "FRICTION", bad)
        self.assertEqual(act({**SB, "softblocks_today": 1}, True), "SOFT_BLOCK")

    def test_one_rung_escalation(self):
        ladder = ["ALLOW", "NUDGE", "DELAY", "FRICTION", "SOFT_BLOCK"]
        for i, prev in enumerate(ladder):
            out = act({**HI, "current_action": prev}, False)
            self.assertLessEqual(ladder.index(out), min(i + 1, 3))

    def test_unknown_history_fails_soft(self):
        for drop in ["current_action", "category", "mins_since_last_intervention"]:
            s = {k: v for k, v in HI.items() if k != drop}
            self.assertIn(act(s, True), ("ALLOW", "NUDGE"), drop)

    def test_invalid_values_do_not_crash(self):
        bad_values = [("hour", None), ("hour", "2"), ("hour", 24), ("hour", -1),
                      ("session_min", None), ("session_min", "10"), ("session_min", -5),
                      ("session_min", float("nan")), ("session_min", float("inf")),
                      ("scroll_per_min", "x"), ("streak_days", True),
                      ("mins_since_last_intervention", "30"), ("recent_overrides", None),
                      ("recent_overrides", -1), ("user_limit_min", "30"), ("user_limit_min", 0)]
        for k, v in bad_values:
            out = decide({**HI, k: v}, True)
            self.assertIn(out["action"], ("ALLOW", "NUDGE"), (k, v))
        self.assertIn(decide("not a dict")["action"], ("ALLOW", "NUDGE"))

    def test_preferred_action_cannot_override_guardrails(self):
        self.assertEqual(act({**HI, "current_action": "ALLOW", "preferred_action": "SOFT_BLOCK"}, True), "NUDGE")
        self.assertEqual(act({**SB, "category": "calls", "preferred_action": "FRICTION"}, True), "ALLOW")
        self.assertEqual(act({**HI, "preferred_action": " delay "}, False), "DELAY")  # allowed + normalised

    def test_cooldown(self):
        self.assertEqual(act({**SB, "mins_since_last_intervention": 3}, True), "ALLOW")


if __name__ == "__main__":
    unittest.main()
