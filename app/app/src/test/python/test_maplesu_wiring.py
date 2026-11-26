"""t12 静态离线核验：GhostSam 最小 MapleSU manager 接线。

不编译 Android（本机无 SDK/NDK），只读取源码/资产做断言。运行：
    py -3 -m unittest discover -s app/app/src/test/python -p 'test_*.py' -v
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]          # ghostsam-normal
SRC = REPO / "app/app/src/main/java/com/snothin/ghostsam"
ASSETS = REPO / "app/app/src/main/assets/payloads/ksud"
MANIFEST = REPO / "app/app/src/main/AndroidManifest.xml"
GRADLE = REPO / "app/app/build.gradle.kts"

MAPLESU_PKG = "dev.maplesu.manager"
MAPLESU_CERT = "30bafb57e3604afe93da13a1285516e043feb82b56a861a157b53ccaa27c3506"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class MapleSuWiringTests(unittest.TestCase):
    def test_manifest_query_has_maplesu(self):
        txt = read(MANIFEST)
        self.assertIn('<package android:name="dev.maplesu.manager" />', txt)
        # 旧三包查询仍在
        for pkg in ("me.weishu.kernelsu", "com.rifsxd.ksunext", "com.sukisu.nova", "com.sukisu.ultra"):
            self.assertIn(f'<package android:name="{pkg}" />', txt)

    def test_maplesu_variant_enum_and_identity(self):
        txt = read(SRC / "data/prefs/KsudVariant.kt")
        self.assertIn("enum class MapleSuVariant", txt)
        self.assertIn("NORMAL", txt)
        self.assertIn('get() = "MapleSU Normal"', txt)
        self.assertIn("fun parse(raw: String?)", txt)
        self.assertIn(f'MANAGER_PACKAGE = "{MAPLESU_PKG}"', txt)
        self.assertIn(MAPLESU_CERT, txt)
        self.assertIn("MANAGER_CERT_DER_SIZE = 772", txt)
        self.assertEqual(64, len(MAPLESU_CERT))

    def test_ksud_variant_enum_unchanged(self):
        txt = read(SRC / "data/prefs/KsudVariant.kt")
        # 原三值枚举保持原样（不得出现第四个 entry）
        self.assertIn("enum class KsudVariant {\n    Default,\n    Next,\n    SukiSU;", txt)

    def test_ksud_assets_mapping_and_failclosed(self):
        txt = read(SRC / "data/exploit/KsudAssets.kt")
        for rel in ("payloads/ksud/ksud", "payloads/ksud/ksud-next", "payloads/ksud/ksud-sukisu"):
            self.assertIn(rel, txt)
        self.assertIn('MAPLESU_ASSET = "payloads/ksud/ksud-maplesu"', txt)
        self.assertIn("fun assetPath(variant: MapleSuVariant): String = MAPLESU_ASSET", txt)
        self.assertIn("fun hasBundledAsset(variant: MapleSuVariant): Boolean = false", txt)
        self.assertIn("requireMapleSuCustomKsud", txt)
        self.assertIn("IllegalStateException", txt)
        self.assertIn("no bundled ksud", txt)
        # assetPath(KsudVariant) 不得把 MapleSU 静默映射到旧 payload
        m = re.search(r"fun assetPath\(variant: KsudVariant\): String = when \(variant\) \{(.*?)\n    \}",
                      txt, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("maplesu", m.group(1).lower())

    def test_no_fake_maplesu_asset(self):
        self.assertFalse((ASSETS / "ksud-maplesu").exists(), "must not add a fake MapleSU payload asset")

    def test_verify_payloads_unchanged(self):
        txt = read(GRADLE)
        # verifyPayloads 仍只要求三套真 payload，未加 maplesu（否则缺文件必失败）
        self.assertIn('require("ksud/ksud")', txt)
        self.assertIn('require("ksud/ksud-next")', txt)
        self.assertIn('require("ksud/ksud-sukisu")', txt)
        self.assertNotIn('require("ksud/ksud-maplesu")', txt)

    def test_kernelsu_probe_covers_maplesu(self):
        txt = read(SRC / "data/device/KernelSuProbe.kt")
        self.assertIn("PACKAGE_MAPLESU", txt)
        self.assertIn("MAPLESU_MANAGER_CERT_SHA256", txt)
        self.assertIn("primaryPackage(variant: MapleSuVariant)", txt)
        self.assertIn("certHash(variant: MapleSuVariant)", txt)
        self.assertIn("currentPackages(variant: MapleSuVariant)", txt)
        self.assertIn("setPackages(variant: MapleSuVariant", txt)
        self.assertIn("managerPackageCandidates(variant: MapleSuVariant", txt)
        self.assertIn("isMapleSuManagerInstalled", txt)
        self.assertIn("probeMapleSuManagerInstalled", txt)
        self.assertIn("preferredMapleSuManagerPackage", txt)
        self.assertIn("fun certDerSize(variant: MapleSuVariant): Int = MapleSuVariant.MANAGER_CERT_DER_SIZE", txt)

    def test_kernelsu_probe_old_variants_intact(self):
        txt = read(SRC / "data/device/KernelSuProbe.kt")
        self.assertIn("private fun primaryPackage(variant: KsudVariant): String = when (variant) {", txt)
        self.assertIn("KsudVariant.Default -> PACKAGE_KERNELSU", txt)
        self.assertIn("KsudVariant.Next -> PACKAGE_KERNELSU_NEXT", txt)
        self.assertIn("KsudVariant.SukiSU -> PACKAGE_SUKISU", txt)
        self.assertIn("private fun certHash(variant: KsudVariant): String? = when (variant) {", txt)
        self.assertIn("KsudVariant.Default -> null", txt)

    def test_settingsprefs_no_silent_default(self):
        txt = read(SRC / "data/prefs/SettingsPrefs.kt")
        self.assertIn('MAPLE_SU_IDENTITY = "maple_su_identity"', txt)
        self.assertIn("fun selectedMapleSuVariant(context: Context): MapleSuVariant?", txt)
        # 关闭时返回 null，而不是 KsudVariant.Default
        self.assertIn("else null", txt)
        seg = txt[txt.index("fun selectedMapleSuVariant"):]
        seg = seg[: seg.index("}") + 1]
        self.assertNotIn("KsudVariant.Default", seg)
        # 原有 ksud_variant 语义未变
        self.assertIn("fun effectiveKsudVariant(context: Context): KsudVariant", txt)

    def test_payload_versions_maplesu_label(self):
        txt = read(SRC / "data/prefs/PayloadVersions.kt")
        self.assertIn("fun label(context: Context, variant: MapleSuVariant): String", txt)
        self.assertIn("需 SAF 自定义 ksud", txt)
        # 旧 fallback 三键仍在
        for k in ("KsudVariant.Default", "KsudVariant.Next", "KsudVariant.SukiSU"):
            self.assertIn(f"{k} to ", txt)

    def test_existing_payloads_unchanged(self):
        lock = json.loads(read(REPO / "payloads.lock.json"))
        for variant, meta in lock["variants"].items():
            f = ASSETS / meta["file"]
            self.assertTrue(f.is_file(), f"missing payload {f}")
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            self.assertEqual(meta["sha256"], h, f"{variant} payload changed")

    # ---- t13: UI / preflight / standalone reachability (fail-closed, no silent fallback) ----

    def test_settings_ui_has_maplesu_optin(self):
        txt = read(SRC / "ui/screen/SettingsScreen.kt")
        self.assertIn("mapleSuEnabled", txt)
        self.assertIn("SettingsPrefs.mapleSuIdentityEnabled(context)", txt)
        self.assertIn("SettingsPrefs.setMapleSuIdentityEnabled(context, checked)", txt)
        self.assertIn("MapleSU Normal（实验性 opt-in）", txt)
        # 旧三变体选择器仍在（关闭 opt-in 后行为不变）
        for v in ("KsudVariant.Default", "KsudVariant.Next", "KsudVariant.SukiSU"):
            self.assertIn(v, txt)

    def test_preflight_maplesu_branch_and_failclosed(self):
        txt = read(SRC / "data/exploit/PreflightResolver.kt")
        self.assertIn("SettingsPrefs.mapleSuIdentityEnabled(context)", txt)
        self.assertIn("KernelSuProbe.probeMapleSuManagerInstalled(context)", txt)
        self.assertIn("KernelSuProbe.probeManagerInstalled(context)", txt)  # 旧分支仍在
        self.assertIn("refusing to fall back to Default/Next/SukiSU", txt)
        self.assertIn("no bundled ksud", txt)
        self.assertIn("no Default fallback", txt)
        self.assertIn("PackSource.Bundled", txt)
        seg = txt[txt.index("val mapleSu = SettingsPrefs.mapleSuIdentityEnabled"):txt.index("// Existing-root check")]
        self.assertNotIn("KsudVariant.Default", seg)

    def test_standalone_dfr_maplesu_branch(self):
        txt = read(SRC / "data/exploit/StandaloneDfr.kt")
        self.assertIn("SettingsPrefs.mapleSuIdentityEnabled(context)", txt)
        self.assertIn("KernelSuProbe.probeMapleSuManagerInstalled(context)", txt)
        self.assertIn("MapleSU Normal: no bundled ksud", txt)
        self.assertIn("custom ksud required (no bundled fallback)", txt)
        self.assertIn("variantLabel", txt)

    def test_maplesu_optin_default_off(self):
        txt = read(SRC / "data/prefs/SettingsPrefs.kt")
        self.assertIn("getBoolean(MAPLE_SU_IDENTITY, false)", txt)


if __name__ == "__main__":
    unittest.main()
