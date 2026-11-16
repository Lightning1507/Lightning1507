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
    for i in range(6):
        user[f"month{i}"] = {field: [
            {"repository": {"id": "public-repo", "isPrivate": False}, "contributions": {"totalCount": 0}},
            {"repository": {"id": "private-repo", "isPrivate": True}, "contributions": {"totalCount": 999}},
        ] for metric, field in activity.FIELDS.items() if metric != "issues"}
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
            self.assertIn("| Commits | PRs | Reviewed PRs | Issues |", updated)
            self.assertIn("| **7** | **7** | **7** | **7** |", updated)

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

    def test_six_months_are_contiguous_with_current_month_partial(self):
        months = activity.recent_months(NOW)
        self.assertEqual(len(months), 6)
        self.assertEqual(activity.timestamp(months[0][0]), "2026-06-01T00:00:00Z")
        self.assertEqual(activity.timestamp(months[-1][0]), "2026-11-01T00:00:00Z")
        self.assertEqual(activity.timestamp(months[-1][1]), activity.timestamp(NOW))
        for previous, following in zip(months, months[1:]):
            self.assertEqual(previous[1] + timedelta(seconds=1), following[0])
        leap = activity.recent_months(datetime(2024, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(activity.timestamp(leap[-2][1]), "2024-02-29T23:59:59Z")
        january = activity.recent_months(datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(activity.timestamp(january[0][0]), "2026-08-01T00:00:00Z")

    def test_public_repository_union_deduplicates_across_metrics(self):
        payload = response()
        payload["data"]["user"]["contributionsCollection"]["issueContributionsByRepository"][0]["repository"]["id"] = "another-public-repo"
        snapshot = activity.build_snapshot(payload, "Lightning1507", NOW)
        self.assertEqual(snapshot["extras"]["public_repositories"], 2)
        self.assertEqual(snapshot["extras"]["merged_pull_requests"], 5)
        self.assertEqual(snapshot["extras"]["opened_pull_requests"], 7)
        self.assertEqual(sum(m["metrics"]["commits"] for m in snapshot["months"]), 0)
        self.assertNotIn("private-repo", str(snapshot))

    def test_missing_month_or_invalid_merged_subset_is_rejected(self):
        payloads=[]
        payload=response(); del payload["data"]["user"]["month4"]; payloads.append(payload)
        payload=response(); payload["data"]["user"]["month2"]["pullRequestContributionsByRepository"][0]["contributions"]["totalCount"]=-1; payloads.append(payload)
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
                self.assertIn("6 months", svg)
                self.assertIn("in progress", svg)
                self.assertIn("GitHub activity", svg)

    def test_monthly_stacks_include_prs_and_reviews_and_hide_private_counts(self):
        payload = response()
        month = payload["data"]["user"]["month5"]
        month["pullRequestContributionsByRepository"][0]["contributions"]["totalCount"] = 4
        month["pullRequestReviewContributionsByRepository"][0]["contributions"]["totalCount"] = 6
        snapshot = activity.build_snapshot(payload, "Lightning1507", NOW)
        self.assertEqual(snapshot["months"][-1]["metrics"], {"commits": 0, "pull_requests": 4, "reviewed_prs": 6})
        self.assertTrue(snapshot["months"][-1]["partial"])
        self.assertNotIn("999", activity.render_panel(snapshot))
        summary = activity.render_activity(snapshot)
        self.assertLess(summary.index('<picture>'), summary.index('<details>'))
        self.assertIn('Reviewed PRs', summary)

    def test_picture_urls_change_when_panel_data_changes(self):
        import re
        snapshot = activity.build_snapshot(response(), "Lightning1507", NOW)
        before = re.findall(r'assets/activity-[^" ]+-[0-9a-f]{12}\.svg', activity.render_activity(snapshot))
        self.assertEqual(len(before), 4)
        snapshot["metrics"]["commits"] += 1
        after = re.findall(r'assets/activity-[^" ]+-[0-9a-f]{12}\.svg', activity.render_activity(snapshot))
        self.assertEqual(len(after), 4)
        self.assertTrue(all(old != new for old, new in zip(before, after)))


if __name__ == "__main__":
    unittest.main()
