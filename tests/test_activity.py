import copy
import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "activity", Path(__file__).parents[1] / "scripts" / "update_activity.py"
)
activity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activity)

NOW = datetime(2026, 11, 16, 2, 17, tzinfo=timezone.utc)


def response(now=NOW):
    user = {"contributionsCollection": {
        field: [
            {"repository": {"id": "public-repo", "isPrivate": False}, "contributions": {"totalCount": 7}},
            {"repository": {"id": "private-repo", "isPrivate": True}, "contributions": {"totalCount": 999}},
        ] for field in activity.FIELDS.values()
    }}
    for i in range(12):
        user[f"week{i}"] = {"commitContributionsByRepository": [
            {"repository": {"id": "public-repo", "isPrivate": False}, "contributions": {"totalCount": 0}},
            {"repository": {"id": "private-repo", "isPrivate": True}, "contributions": {"totalCount": 999}},
        ]}
    return {"data": {"user": user, "opened": {"issueCount": 7}, "merged": {"issueCount": 5}}}


class ActivityTests(unittest.TestCase):
    def test_private_contributions_are_excluded(self):
        snapshot = activity.build_snapshot(response(), "Lightning1507", NOW)
        self.assertEqual(set(snapshot["metrics"].values()), {7})
        self.assertNotIn("999", activity.render_activity(snapshot))

    def test_empty_public_activity_is_a_real_zero(self):
        payload = response()
        for entries in payload["data"]["user"]["contributionsCollection"].values():
            entries.clear()
        self.assertEqual(set(activity.build_snapshot(payload, "Lightning1507", NOW)["metrics"].values()), {0})

    def test_missing_or_invalid_data_fails_instead_of_becoming_zero(self):
        invalid = [
            {"errors": [{"message": "permission denied"}]},
            {"data": {"user": None}},
        ]
        for value in [None, -1, True, "12"]:
            payload = response()
            field = next(iter(activity.FIELDS.values()))
            payload["data"]["user"]["contributionsCollection"][field][0]["contributions"]["totalCount"] = value
            invalid.append(payload)
        payload = response()
        del payload["data"]["user"]["contributionsCollection"][next(iter(activity.FIELDS.values()))]
        invalid.append(payload)
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                activity.build_snapshot(payload, "Lightning1507", NOW)

    def test_unknown_repository_visibility_is_rejected(self):
        payload = response()
        field = next(iter(activity.FIELDS.values()))
        del payload["data"]["user"]["contributionsCollection"][field][0]["repository"]["isPrivate"]
        with self.assertRaises(ValueError):
            activity.build_snapshot(payload, "Lightning1507", NOW)

    def test_repository_limit_is_not_silently_truncated(self):
        payload = response()
        field = next(iter(activity.FIELDS.values()))
        entry = payload["data"]["user"]["contributionsCollection"][field][0]
        payload["data"]["user"]["contributionsCollection"][field] = [copy.deepcopy(entry) for _ in range(100)]
        with self.assertRaises(ValueError):
            activity.build_snapshot(payload, "Lightning1507", NOW)

    def test_year_rollover_uses_utc(self):
        now = datetime(2027, 1, 1, 0, 1, tzinfo=timezone.utc)
        snapshot = activity.build_snapshot(response(now), "Lightning1507", now)
        self.assertEqual(snapshot["period_start"], "2027-01-01T00:00:00Z")
        self.assertIn("2027", activity.render_activity(snapshot))

    def test_failed_fetch_preserves_previous_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Lightning\n\n<!-- activity:start -->\nold\n<!-- activity:end -->\n", encoding="utf-8")
            activity.write_outputs(activity.build_snapshot(response(), "Lightning1507", NOW), root)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with patch.object(activity, "fetch_payload", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    activity.refresh("Lightning1507", "not-a-real-token", root, NOW)
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_update_preserves_text_outside_the_activity_block(self):
        original = "# Lightning\nMy introduction.\n<!-- activity:start -->\nold counts\n<!-- activity:end -->\nMy projects.\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(original, encoding="utf-8")
            activity.write_outputs(activity.build_snapshot(response(), "Lightning1507", NOW), root)
            updated = (root / "README.md").read_text(encoding="utf-8")
            self.assertTrue(updated.startswith("# Lightning\nMy introduction.\n"))
            self.assertTrue(updated.endswith("\nMy projects.\n"))
            self.assertNotIn("old counts", updated)
            self.assertIn("**7** commits", updated)

    def test_missing_or_duplicate_markers_preserve_both_outputs(self):
        for content in ["No marker", "<!-- activity:start -->", "<!-- activity:end -->",
                        "<!-- activity:end --><!-- activity:start -->",
                        "<!-- activity:start --><!-- activity:end --><!-- activity:start --><!-- activity:end -->"]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "README.md").write_text(content, encoding="utf-8")
                (root / "data").mkdir()
                (root / "data" / "activity.json").write_text("previous data", encoding="utf-8")
                with self.assertRaises(ValueError):
                    activity.write_outputs(activity.build_snapshot(response(), "Lightning1507", NOW), root)
                self.assertEqual((root / "README.md").read_text(encoding="utf-8"), content)
                self.assertEqual((root / "data" / "activity.json").read_text(encoding="utf-8"), "previous data")

    def test_saved_snapshot_cannot_change_scope_or_inject_markup(self):
        snapshot = activity.build_snapshot(response(), "Lightning1507", NOW)
        snapshot["scope"] = "all repositories"
        with self.assertRaises(ValueError):
            activity.validate_snapshot(snapshot)

        snapshot = activity.build_snapshot(response(), "Lightning1507", NOW)
        snapshot["username"] = '<script>alert(1)</script>'
        with self.assertRaises(ValueError):
            activity.validate_snapshot(snapshot)

    def test_completed_weeks_are_utc_monday_to_sunday_without_overlap(self):
        weeks = activity.completed_weeks(NOW)
        self.assertEqual(len(weeks), 12)
        self.assertEqual(activity.timestamp(weeks[-1][0]), "2026-11-09T00:00:00Z")
        self.assertEqual(activity.timestamp(weeks[-1][1]), "2026-11-15T23:59:59Z")
        for previous, following in zip(weeks, weeks[1:]):
            self.assertEqual(previous[1] + timedelta(seconds=1), following[0])
        # Sunday in UTC+7 is still part of the week that has not finished in UTC.
        sunday = datetime(2026, 11, 15, 23, 30, tzinfo=timezone(timedelta(hours=7)))
        self.assertEqual(activity.timestamp(activity.completed_weeks(sunday)[-1][1]), "2026-11-08T23:59:59Z")

    def test_public_repository_union_deduplicates_across_metrics(self):
        payload = response()
        payload["data"]["user"]["contributionsCollection"]["issueContributionsByRepository"][0]["repository"]["id"] = "another-public-repo"
        snapshot = activity.build_snapshot(payload, "Lightning1507", NOW)
        self.assertEqual(snapshot["extras"]["public_repositories"], 2)
        self.assertEqual(snapshot["extras"]["merged_pull_requests"], 5)
        self.assertEqual(snapshot["extras"]["opened_pull_requests"], 7)
        self.assertEqual(sum(w["commits"] for w in snapshot["weeks"]), 0)
        self.assertNotIn("private-repo", str(snapshot))

    def test_missing_week_or_invalid_merged_subset_is_rejected(self):
        payloads=[]
        payload=response(); del payload["data"]["user"]["week4"]; payloads.append(payload)
        payload=response(); payload["data"]["merged"]["issueCount"]=8; payloads.append(payload)
        payload=response(); payload["data"]["opened"]["issueCount"]=True; payloads.append(payload)
        for index, payload in enumerate(payloads):
            with self.subTest(case=index), self.assertRaises(ValueError):
                activity.build_snapshot(payload, "Lightning1507", NOW)

    def test_zero_values_render_valid_svg_and_numeric_accessible_text(self):
        import xml.etree.ElementTree as ET
        payload=response()
        payload["data"]["opened"]["issueCount"]=0
        payload["data"]["merged"]["issueCount"]=0
        snapshot=activity.build_snapshot(payload, "Lightning1507", NOW)
        for theme in ("light", "dark"):
            for mobile in (False, True):
                svg=activity.render_panel(snapshot, theme, mobile)
                ET.fromstring(svg)
                self.assertNotIn("nan", svg.lower())
                self.assertIn("0 of 0", svg)
                self.assertIn("12 completed weeks", svg)


if __name__ == "__main__":
    unittest.main()
