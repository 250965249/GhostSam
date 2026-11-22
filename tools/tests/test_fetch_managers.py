from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch_managers import parse_uapi, pick_release, release_revision


class ManagerSelectionTests(unittest.TestCase):
    def test_release_tag_resolves_lightweight_and_annotated_commits(self):
        commit = 'a' * 40
        self.assertEqual(commit, release_revision('official/repo', 'v1.0', lambda path: {'object': {'type': 'commit', 'sha': commit}}))
        tag = 'b' * 40
        def api(path):
            return {'object': {'type': 'commit', 'sha': commit}} if '/git/tags/' in path else {'object': {'type': 'tag', 'sha': tag}}
        self.assertEqual(commit, release_revision('official/repo', 'v1.0', api))

    def test_release_tag_cannot_resolve_to_a_tree_or_loop_forever(self):
        for kind in ('tree', 'tag'):
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                release_revision('official/repo', 'v1.0', lambda path: {'object': {'type': kind, 'sha': 'b' * 40}})

    def test_real_uapi_const_and_legacy_macro_are_recognized(self):
        self.assertEqual(6, parse_uapi("static const __u32 KERNEL_SU_UAPI_VERSION = 6;"))
        self.assertEqual(5, parse_uapi("#define KERNEL_SU_UAPI_VERSION 5"))
        self.assertEqual(6, parse_uapi("static const __u32 KERNEL_SU_UAPI_VERSION = 0x6U;"))

    def test_uapi_comments_and_ambiguous_declarations_are_not_trusted(self):
        for text in ("// static const __u32 KERNEL_SU_UAPI_VERSION = 6;", "unknown", "#define KERNEL_SU_UAPI_VERSION 0", "#define KERNEL_SU_UAPI_VERSION 5\nstatic const __u32 KERNEL_SU_UAPI_VERSION = 6;"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                parse_uapi(text)

    def test_pinned_tag_selects_only_the_pinned_release(self):
        assets = [{"name": "SukiSU_Nova_v3.0.0_35000-arm64-v8a-release.apk"}]
        with patch("fetch_managers.release_assets", return_value=assets) as release_assets:
            release, how, asset = pick_release(
                "SukiSU-Nova/SukiSU-Nova",
                [{"tagName": "v4.0.0", "isPrerelease": False}],
                "4.3.0-r2",
                pinned_tag="v3.0.0",
            )
        self.assertEqual("v3.0.0", release["tagName"])
        self.assertEqual("exact", how)
        self.assertEqual(assets[0], asset)
        release_assets.assert_called_once_with("SukiSU-Nova/SukiSU-Nova", "v3.0.0")

    def test_pinned_tag_does_not_fall_back_to_newest_release(self):
        with patch("fetch_managers.release_assets", return_value=[]):
            release, how, asset = pick_release(
                "SukiSU-Nova/SukiSU-Nova",
                [{"tagName": "v4.0.0", "isPrerelease": False}],
                "4.3.0-r2",
                pinned_tag="v3.0.0",
            )
        self.assertIsNone(release)
        self.assertEqual("none", how)
        self.assertIsNone(asset)

    def test_unpinned_selection_keeps_exact_then_stable_order(self):
        def assets(repo, tag):
            return [{"name": "manager-release.apk"}] if tag == "v4.3.0-r2" else []

        releases = [
            {"tagName": "v4.0.0", "isPrerelease": False},
            {"tagName": "v4.3.0-r2", "isPrerelease": False},
        ]
        with patch("fetch_managers.release_assets", side_effect=assets):
            release, how, asset = pick_release(
                "producer/repo", releases, "4.3.0-r2"
            )
        self.assertEqual("v4.3.0-r2", release["tagName"])
        self.assertEqual("exact", how)
        self.assertEqual("manager-release.apk", asset["name"])


if __name__ == "__main__":
    unittest.main()
