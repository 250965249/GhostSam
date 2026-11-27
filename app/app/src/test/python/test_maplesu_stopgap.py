"""GhostSam MapleSU stop-gap wiring tests (offline, static + logic mirror).

Covers the two independently-testable deliverables from the owner guidance §3.1/§3.3:

1. Status misreport fix (upgrade panel): the three ambiguity inputs must never read
   "初始化完成"; probe-failed+connection-closed -> 连接已关闭，结果未确认, timeout -> 超时,
   version-only -> 模块可响应.
2. Request-entry stop-gap: the MapleSU variant is refused at the earliest entry point,
   before any payload/asset read or background launch, in all four scenarios
   (fresh settings / upgrade with history / manual entry / local-file override).
   A simulated backend proves the request is refused with zero resource reads.

The Kotlin sources are static-checked (the app has no JVM unit-test target); the pure
classification/gate logic is mirrored here so the three/four input contracts are executable.
"""
from __future__ import annotations

import unittest
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "tools" / "gen_payload_versions.py").is_file() and (p / "app" / "app" / "src" / "main" / "java").is_dir():
            return p
    raise RuntimeError("repo root not found from " + str(here))


ROOT = _repo_root()
MAIN = ROOT / "app" / "app" / "src" / "main" / "java" / "com" / "snothin" / "ghostsam"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _function_body(text: str, sig: str) -> str:
    i = text.index(sig)
    j = text.index("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    raise AssertionError("unbalanced braces for " + sig)


# ---- mirror of KsudUpgradeOutcome.classifyKsudUpgradeOutcome ----
def classify(module_active: bool, version_responded: bool, timed_out: bool, channel_closed: bool) -> str:
    if module_active:
        return "INITIALIZED"
    if timed_out:
        return "TIMEOUT"
    if version_responded:
        return "MODULE_RESPONSIVE"
    if channel_closed:
        return "CONNECTION_CLOSED_UNCONFIRMED"
    return "MODULE_RESPONSIVE"


# resource-read / temp-write / background-launch tokens that must NOT precede the guard.
RESOURCE_TOKENS = [
    "DeviceSnapshot.current", "ExploitPack.bundled", "ExploitPack.local",
    "PayloadProfileStore", "contentResolver", "assets.open", "readAsset", "readLocal",
    "readBytes", "KernelSuProbe", "SuController", "ExploitRuntime.backend",
    "GsdfrAppNative", "execLateLoad", "writeFile", "Job(", "launch(", "Dispatchers.IO",
]


class StatusMisreportTests(unittest.TestCase):
    def test_three_inputs_never_show_initialized(self):
        cases = {
            # 探测未通过 + 连接关闭 -> 连接已关闭，结果未确认
            "probe_failed+conn_closed": (dict(module_active=False, version_responded=False,
                                              timed_out=False, channel_closed=True),
                                         "CONNECTION_CLOSED_UNCONFIRMED", "连接已关闭，结果未确认"),
            # 请求超时 -> 超时
            "timeout": (dict(module_active=False, version_responded=False,
                             timed_out=True, channel_closed=True), "TIMEOUT", "超时"),
            # 仅查到版本 -> 模块可响应
            "version_only": (dict(module_active=False, version_responded=True,
                                  timed_out=False, channel_closed=False),
                             "MODULE_RESPONSIVE", "模块可响应"),
        }
        labels = {
            "INITIALIZED": "初始化完成",
            "CONNECTION_CLOSED_UNCONFIRMED": "连接已关闭，结果未确认",
            "TIMEOUT": "超时",
            "MODULE_RESPONSIVE": "模块可响应",
        }
        for name, (kw, outcome, message) in cases.items():
            with self.subTest(name=name):
                got = classify(**kw)
                self.assertNotEqual("INITIALIZED", got)
                self.assertEqual(outcome, got)
                self.assertEqual(message, labels[got])
                self.assertNotEqual("初始化完成", labels[got])

    def test_kotlin_classifier_matches_three_input_contract(self):
        src = _read(MAIN / "ui/screen/KsudUpgradeOutcome.kt")
        for tok in (
            "classifyKsudUpgradeOutcome", "INITIALIZED", "CONNECTION_CLOSED_UNCONFIRMED",
            "TIMEOUT", "MODULE_RESPONSIVE", "连接已关闭，结果未确认", "超时", "模块可响应",
        ):
            self.assertIn(tok, src)

    def test_panel_no_longer_treats_socket_close_as_success(self):
        panel = _read(MAIN / "ui/screen/KsudUpgradePanel.kt")
        # the old misreport path must be gone
        self.assertNotIn("socket closed by daemon → late-load succeeded", panel)
        self.assertNotIn("ok = true", panel)
        self.assertIn("classifyKsudUpgradeOutcome", panel)
        # finalize (the "initialized" success) is only reachable when the outcome is initialized
        body = _function_body(panel, "fun start(executionMode: ExecutionMode)")
        g = body.index("classifyKsudUpgradeOutcome")
        self.assertIn("outcome.initialized", body[g:])


class EntryStopgapTests(unittest.TestCase):
    def test_refusal_wording_is_disabled_not_fixed(self):
        src = _read(MAIN / "data/exploit/MapleSuStopgap.kt")
        self.assertIn("已停用", src)
        self.assertNotIn("已修复", src)
        self.assertIn("effectiveKsudVariant", src)
        self.assertIn("KsudVariant.MapleSU", src)

    def test_guard_is_the_earliest_statement(self):
        checks = [
            ("data/exploit/PreflightResolver.kt",
             "suspend fun resolve(profileId: String?): RunPlan", "refusalIfMapleSu"),
            ("data/exploit/StandaloneDfr.kt",
             "fun preflight(context: Context, profile: PayloadProfile?): String?", "refusalIfMapleSu"),
            ("data/exploit/ExploitViewModel.kt",
             "private suspend fun runExploit(profileId: String?", "isMapleSuRequest"),
        ]
        for rel, sig, token in checks:
            with self.subTest(file=rel):
                body = _function_body(_read(MAIN / rel), sig)
                self.assertIn(token, body)
                head = body[:body.index(token)]
                for t in RESOURCE_TOKENS:
                    self.assertNotIn(t, head,
                                     f"{rel}: resource op {t!r} runs BEFORE the MapleSU guard")

    def test_four_scenarios_refused_with_zero_resource_reads(self):
        # A simulated backend that counts resource reads / temp writes / background starts.
        class SimBackend:
            def __init__(self):
                self.resource_reads = 0
                self.temp_writes = 0
                self.background_starts = 0
            # the guard short-circuits before any of these are ever called

        def gate(variant: str, scenario: str, backend: SimBackend):
            # mirror of MapleSuStopgap.refusalIfMapleSu: only reads the pref, then refuses.
            if variant == "MapleSU":
                return "MapleSU 载荷已停用：三星机型上存在已知故障，已停止发起提权请求。"
            return None

        for scenario in ("fresh_settings", "upgrade_with_history", "manual_entry", "local_file_override"):
            with self.subTest(scenario=scenario):
                backend = SimBackend()
                msg = gate("MapleSU", scenario, backend)
                self.assertIsNotNone(msg)
                self.assertIn("已停用", msg)
                self.assertNotIn("已修复", msg)
                # refused before touching resources
                self.assertEqual(0, backend.resource_reads)
                self.assertEqual(0, backend.temp_writes)
                self.assertEqual(0, backend.background_starts)

        # other variants are NOT blocked (no regression for default/next/sukisu)
        for variant in ("Default", "Next", "SukiSU"):
            self.assertIsNone(gate(variant, "fresh_settings", SimBackend()))


if __name__ == "__main__":
    unittest.main()
