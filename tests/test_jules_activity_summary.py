"""Regression coverage for normalized Jules activity summaries."""

from __future__ import annotations

import unittest

from src.api.jules_client import JulesClient


class JulesActivitySummaryTests(unittest.TestCase):
    """Ensure provider activity stays legible in the Studio conversation."""

    def test_describes_plan_steps_progress_and_changed_files(self) -> None:
        """The browser receives meaningful summaries without raw provider payloads."""

        plan = JulesClient.describe_activity(
            {
                "name": "sessions/example/activities/plan",
                "planGenerated": {"plan": {"steps": [{"title": "Inspect"}, {"title": "Edit"}]}},
            }
        )
        progress = JulesClient.describe_activity(
            {
                "name": "sessions/example/activities/progress",
                "progressUpdated": {"title": "Running checks"},
            }
        )
        change = JulesClient.describe_activity(
            {
                "name": "sessions/example/activities/change",
                "changeSet": {
                    "gitPatch": {
                        "unidiffPatch": "diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n--- a/src/a.py\n+++ b/src/b.py\n"
                    }
                },
            }
        )

        self.assertEqual(plan["kind"], "plan.generated")
        self.assertEqual(plan["summary"], "Jules generated a 2-step implementation plan.")
        self.assertEqual(progress["kind"], "progress.updated")
        self.assertEqual(progress["summary"], "Running checks")
        self.assertEqual(change["kind"], "code.changed")
        self.assertEqual(change["summary"], "Jules updated src/a.py, src/b.py.")


if __name__ == "__main__":
    unittest.main()
