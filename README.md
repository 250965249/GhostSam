# GhostSam

**English** | [中文](README.zh.md)

Kernel privilege-escalation toolkit for Samsung Galaxy S22 / S26 / Z Fold8
devices (Android 12 and up, kernels 5.10-6.18), with an Android manager app
that drives the whole flow over ADB.

The tree ships three device-specific Ghostlock exploit chains (race-based),
a dirtyfrag install chain (matched by KMI), a shared userspace layer, and
the app that packages the prebuilt payloads.

## Overview

- **Target devices**: Galaxy S22 (`s22`), Galaxy S26 (`s26`) and Galaxy
  Z Fold8 (`zf8`), on Snapdragon and Exynos SoCs.
- **Install paths**:
  - **dirtyfrag (recommended, faster)**: a single run deploys the KernelSU
    kernel module (page-cache 4-byte store via xfrm-ESP, CVE-2026-43284)
    and late-loads it directly; there is no race to win.
  - **Ghostlock chains**: race-based exploits for the selected series; the
    device reboots during the run, after which the app completes the
    KernelSU late-load.
- **Memory-corruption primitive (CVE-2026-43499)**: a futex PI use-after-free
  race is turned into a single controlled 8-byte kernel write; a `misc` /
  `simple_attr` file descriptor serves as the write target, from which
  arbitrary kernel read/write is built. The Z Fold8 chain uses an `io_submit`
  based write path.
- **Root stage**: a forged `work_struct` is queued on `system_unbound_wq`
  with `call_usermodehelper_exec_work` as its function, so the kernel
  starts our helper with init credentials; no credential structure is
  modified, and KDP's EL2 guard sees nothing.
- **Root channel**: `su_daemon` keeps a root shell on
  `/data/local/tmp/temp_su.sock` for the session.
- **Persistence (temporary root)**: KernelSU or KernelSU-Next is
  late-loaded; root is kept until the next reboot (re-run the chain to
  re-apply). In the Ghostlock chains the `ksud` binary is bind-mounted over
  a dormant system binary, so path-based DEFEX rules only ever see a
  whitelisted target.

## Supported devices

### Ghostlock chains (matched by device)

Firmware is matched per kernel baseline, not per build number. Every series
ships a generated table: one entry per kernel baseline (17-18 symbol
offsets) and one row per known build; matching happens at runtime on
series / codename / model / build, and unknown combinations fail closed
(no kernel writes are attempted).

| Series  | Baselines | Known builds | Devices / notes                                               |
| ------- | --------- | ------------ | ------------------------------------------------------------- |
| s22     | 4         | 66           | SM-S9010 / S9060 / S9080 ... (Snapdragon, Exynos 2200)        |
| s26     | 4         | 153          | SM-S9420 / S9470 / S9480 ... (Snapdragon, Exynos), One UI 8.5 |
| s26-ui9 | 2         | 6            | One UI 9 geometry, executed by the io_submit chain            |
| zf8     | 6         | 73           | SM-F9710 / F9760 ... (Z Fold8)                                |

A custom baseline for unlisted firmware can be supplied at runtime through
`/data/local/tmp/ghostsam-lines.conf`; the format is validated by
`exploit/src/common/params_custom.h`.

### dirtyfrag chain (matched by KMI)

The dirtyfrag chain ships one kernel module per KMI (`dfr_lkm-<kmi>.ko` in
the app assets); the app selects the module matching the device's kernel.
Shipped KMIs:

| KMI            | Status   | Notes                                       |
| -------------- | -------- | ------------------------------------------- |
| android12-5.10 | Tested   |                                             |
| android13-5.10 | Untested |                                             |
| android13-5.15 | Tested   |                                             |
| android14-5.15 | Untested |                                             |
| android14-6.1  | Partial  | only kernels ≤6.1.71; 6.1.72+ not supported |
| android15-6.6  | Tested   |                                             |
| android16-6.12 | Tested   |                                             |
| android17-6.18 | Untested |                                             |

## Repository layout

```
app/                             Android manager app (Kotlin / Jetpack Compose)
  app/src/main/assets/payloads/  prebuilt payloads staged into the APK
exploit/
  src/common/                    shared layer: logging, options, parameter
                                 tables, KernelSnitch probe, KSU protocol,
                                 su_daemon, boot log, chain skeleton
  src/chains/pselect/            pselect write chain + attr carrier + UMH root
  src/chains/iosubmit/           io_submit write chain (zf8, s26 on One UI 9)
  src/chains/dirtyfrag/          xfrm-ESP page-cache store installer + LKM
                                 loader
  src/series/s22|s26|zf8/        per-series entry points, parameter tables,
                                 and assembly
```

## Building

### App

All payloads are prebuilt under `app/app/src/main/assets/payloads/`, so the
APK builds standalone:

```bash
cd app
./gradlew assembleRelease
```

The `:app:verifyPayloads` Gradle task validates the payload set during the
build: each series folder must provide `preload.so` and `su_daemon`,
`payloads/ksud/` must provide `ksud` and `ksud-next`, and
`payloads/dirtyfrag/` must provide `dfr_payload` plus one
`dfr_lkm-<kmi>.ko` for every supported KMI.

### Native payloads

Building the native side needs an Android NDK r30 (or a host clang with an
aarch64 sysroot). `exploit/Makefile` is the single build entry point;
artifacts land under `exploit/build/`:

```bash
cd exploit
make            # all series + dirtyfrag payloads
make s26        # or a single target: s26 / s22 / zf8 / dirtyfrag
```

### Dirtyfrag kernel module

The LKM is an out-of-tree kbuild target and needs a prepared kernel build
directory; Android DDK containers provide one per KMI:

```bash
make lkm KDIR=/path/to/kernel-build-dir
```

### ksud binaries

`payloads/ksud/ksud` and `payloads/ksud/ksud-next` are prebuilt release
builds (aarch64-linux-android) from our KernelSU forks,
[snothin/KernelSU](https://github.com/snothin/KernelSU) and
[snothin/KernelSU-Next](https://github.com/snothin/KernelSU-Next); rebuild
them from those repositories when bumping the version.

## Usage

1. Install the APK on a supported device (or build it as shown above).
2. Enable USB debugging and connect over ADB.
3. In the app, pick the install path:
   - **dirtyfrag (recommended)**: a single run - the chain deploys and
     late-loads the KernelSU module directly.
   - **Ghostlock chains**: the race-based exploit for the selected series;
     the device reboots during the run, and the app then performs the
     KernelSU late-load.

## Runtime options

The payloads read a small set of `GHOSTSAM_*` environment variables injected
by the app over ADB - for example `GHOSTSAM_NO_KSU`, `GHOSTSAM_KSU_DEFER`
and `GHOSTSAM_PARAMS_STRICT`. The authoritative list and semantics live in
the payload sources (`exploit/src/common/options.*`); the app UI exposes the
safe subset.

## Relationship to DFRoot

[DFRoot](https://github.com/diabl0w/DFRoot) by @diabl0w is a focused,
minimal implementation of the public DirtyFrag technique. GhostSam
integrates the earlier work (the Ghostlock exploit chains for S22 / S26 /
Z Fold8) into one toolkit, so the project is larger and more involved
overall - yet its dirtyfrag path is still fast: a single run deploys and
late-loads KernelSU. Give it a try; feedback, issues and PRs are welcome.

Both implementations build on the same public technique; see
[NOTICE](NOTICE) for the provenance statement.

## Disclaimer

This repository is published for security research and education only, and
targets devices you own and control. Running the Ghostlock chains reboots
the device, may leave SELinux permissive for the session (a Knox watchdog
may force a reboot), and can permanently brick a device if a wrong kernel
baseline is forced. You are responsible for your own device and for
complying with applicable law.

## Credits

- **s26 series** - merged from [ghostlock-s26](https://github.com/snothin/ghostlock-s26);
  its README carries the full credit list.
- **Z Fold8 transplant input (io_submit primer)** - from @diabl0w.
- **ksud / ksud-next** - built from [snothin/KernelSU](https://github.com/snothin/KernelSU)
  and [snothin/KernelSU-Next](https://github.com/snothin/KernelSU-Next), forks that
  track upstream and incorporate work from multiple developers in the KernelSU
  community.
- **dirtyfrag chain** - follows the public DirtyFrag technique; the provenance
  statement is in [NOTICE](NOTICE).

## License

Apache-2.0 - see [LICENSE](LICENSE). Third-party notices and
acknowledgements are in [NOTICE](NOTICE).

---

## Fork Build & Maintenance (maintained by 250965249)

> This section documents the fork's self-built three-variant Samsung producer chains and the GhostSam consumer side, as a self-contained long-term maintenance reference. The upstream snothin project description above is left unchanged.

### 1. Four repositories and the daily schedule

The fork splits "Samsung-adapted kernel builds" from "APK packaging" into a producer / consumer split: three producers each merge upstream and emit reusable payloads; GhostSam only consumes and packages.

| Role | Repository | Branch | Synced upstream (baseline example) | Current baseline example |
| --- | --- | --- | --- | --- |
| consumer | `250965249/GhostSam` | `bakasu` | — | consumes all three variants into the APK |
| producer | `250965249/KernelSU-Next` | `samsung` | `KernelSU-Next/KernelSU-Next@dev` (UAPI 6) | variant=`next` |
| producer | `250965249/KernelSU` | `samsung` | `tiann/KernelSU@main` (UAPI 6) | variant=`default` |
| producer | `250965249/SukiSU-Ultra` | `samsung` | `SukiSU-Nova/SukiSU-Nova@main` | variant=`sukisu` |

> Upstream refs above are stable example baselines; exact HEADs, version numbers and run ids are always **subject to the repository's current state** (these move fast and are not hard-coded in the docs).

Daily schedule (Asia/Shanghai):

- **02:00**: each of the three producers runs `auto-sync-build` — merge its upstream → 8-KMI dual-arch DDK LKM + ksuinit + aarch64 ksud → publish `latest-ksud` release (`ksud-<variant>-aarch64` + `.version` sidecar).
- **05:00 / 09:00 / 13:00**: GhostSam `auto-build` pulls the three variants + sidecars → rebuilds `versions.properties` / `payloads.lock.json` → builds the APK → publishes an `auto-<timestamp>` release (with the three official manager APKs: KernelSU / KernelSU-Next / SukiSU).
- The two layers are decoupled: producers only emit payloads, GhostSam only consumes. If any variant lacks a sidecar, is behind upstream, or its version stamp disagrees with the tree, the tail gate turns red after publishing instead of silently shipping a stale package.

### 2. The three downstream branches

| variant | Baseline (upstream) | Macro set (build-line red line) | SAMSUNG_OWNED | Notes |
| --- | --- | --- | --- | --- |
| `next` | official dev (UAPI 6) | `CONFIG_KSU_SAMSUNG_KDP / RKP / DEFEX / NO_PATCH_TEXT` (four macros) | 17 files (11 kernel + 6 ksud late-load) | rebuilt from official dev + snothin Samsung patch set |
| `default` | tiann main (UAPI 6) | `CONFIG_KSU_RKP_NO_PATCH_TEXT / CONFIG_KSU_SAMSUNG_KDP / CONFIG_KSU_SAMSUNG_DEFEX` (three macros; names differ from the other two trees) | 21 files | dual LKM naming `{kmi}_kernelsu.ko` + `{kmi}-exynos_kernelsu.ko`, resolved by both late_load `KSUD_TREE=exynos` and boot-patch paths |
| `sukisu` | SukiSU-Nova main | four macros (same as `next`) | see producer CI | `selinux_hide` five-point kprobe |

- **Macro names are a red line**: the vanilla tree spells NO_PATCH_TEXT as `CONFIG_KSU_RKP_NO_PATCH_TEXT` (no `SAMSUNG_` prefix) and has no standalone `CONFIG_KSU_SAMSUNG_RKP`; the ddk build line and `kernel/Kbuild` gates must use each tree's own names. Copying the other trees silently skips `samsung_rkp.c`, making the manager report "not installed".
- `next` / `default` must satisfy GhostSam's full samsung-ci contract; `sukisu` goes through a release special case, with device regression covered in section 5.

### 3. Samsung adaptation methodology

- **Three Knox defenses**: KDP (memory holding `cred` and similar sensitive structures is EL2-protected; use Samsung-safe memory / read-only pass-through interfaces), RKP (monitors kernel text and page tables, forbids runtime writes to read-only code; a violation triggers an EL2 panic/reboot), DEFEX (kernel whitelist blocks direct execution of files/modules under `/data`; stage into `/dev` or memfd). These map to the KDP / RKP / DEFEX / NO_PATCH_TEXT macros.
- **`NO_PATCH_TEXT` rule**: under Samsung RKP every `ksu_patch_text` / `ksu_lsm_hook` surface returns `-EOPNOTSUPP`. Each patch point must either find a kprobe replacement or be explicitly disabled and documented (e.g. `mount_hide` stays disabled, matching the historical baseline).
- **selinux_hide five points**: `context` / `access` / `setprocattr` / `status` / `inode_setxattr`, each with its own `samsung_*_kprobe`. **Single-point failure degradation principle**: a failed kprobe registration at any point only `pr_err`s and degrades that point — never a global `unhook`. 2026-11 lesson: upstream added the `inode_setxattr` hide surface with no Samsung channel, and a last-step failure once tore down all four already-installed points, fully disabling SELinux hide and getting caught by detection tools.
- **init.c order**: `ksu_init_symbol_resolver()` → `ksu_samsung_kdp_init()` → `prepare_creds()` (rollback on failure) → `ksu_samsung_defex_init()` → upstream-added init calls (e.g. `ksu_selinux_inode_init` / `ksu_preinit_init`); exit is symmetric.
- **LKM embedded at build time**: the kernel module is packed into the `ksud` binary by RustEmbed/`include_bytes!` during build; updating the LKM requires rebuilding `ksud`, not just swapping the `.ko`.
- **Regression rule**: whenever upstream adds a hide surface (a new `ksu_patch_text` / `ksu_lsm_hook` call site), a Samsung channel or an explicit decision must be added and recorded in the audit table.

### 4. Maintenance quick reference

Version formulas (**dynamically grep `userspace/ksud/build.rs`; never hard-code**, upstream changed them several times):

| variant | Formula | Notes |
| --- | --- | --- |
| `next` | `VERSION_CODE = 30000 + git rev-list --count HEAD` | |
| `default` | `VERSION_CODE = 30000 + git rev-list --count HEAD` | |
| `sukisu` | `VERSION_CODE = 30996 + git rev-list --count HEAD` | `30000 + 996` in build.rs |

- **Version authority** is the `KSU_NO_NEW_PRIVS<5-digit code><name>` stamp in the binary's `.rodata`; `versions.properties` is generated by reading it back and cross-checked against the sidecar, failing on any mismatch.
- **Sidecar contract**: `.version` carries `build_kind=samsung-ci`, `kernel_version_code`, `kernel_uapi_version`, `official_ci_commit` (official CI commit, 40 hex, used to lock the official manager), `producer_run_id`, `producer_artifact_id`, `producer_artifact_digest`, plus `behind_upstream`; the kernel UAPI must equal the official manager's.
- **Conflict handling**: inside the `SAMSUNG_OWNED` whitelist, `git merge -X ours` runs automatically; outside it, the run goes red; if `merge_auto_resolved` touches any `kernel/` path, GhostSam rejects that day's product for the variant (a safety design — kernel conflicts must not be silent), requiring a manual three-way merge and re-dispatch.
- **Fork schedule gotcha**: Actions permission enabled ≠ schedule activated; a fork's scheduled workflow usually needs the owner to click the Enable banner once on the Actions page, otherwise cron stays silent.
- **Known pitfalls**: `GITHUB_TOKEN` lacks workflows permission so it cannot push tags/workflows; `git fetch --tags` overrides the refspec and re-imports non-v tags (polluting `git describe --tags`); `actions/checkout` must use `ref: ${{ github.ref_name }}`, not `github.sha` (otherwise it builds the previous round's tree); `gh release upload --clobber` has a 422 race — delete then upload; `find … | head` trips SIGPIPE under `pipefail` — use `-print -quit`.

### 5. Regression checklist and a case study

After each upstream sync, verify: sidecar `behind_upstream=0`, binary version stamp matching the tree, the LKM artifact carrying Samsung strings (KDP/DEFEX/RKP sucompat/selinux_hide), and the `selinux_hide` five-point product-level check (5 `samsung_*_kprobe` structs and registered flags actually compiled in).

**Case study (41179, 2026-11)**: upstream `5b6262e8` added a fifth disguise point on `selinux_inode_setxattr`, while the Samsung fork covered only four points; the fifth still went through `ksu_lsm_hook → ksu_patch_text`, which always fails under `NO_PATCH_TEXT`, triggering a global `unhook` in `ksu_selinux_hide_enable()` — SELinux hide went fully dark and was caught by detection tools (LSPosed / SELinux leak). The fix = add the RKP kprobe channel for `inode_setxattr` + make single-point failure non-fatal, and fix the CI `manager-ci` tag pollution of `git describe`. See [CI_UPDATES.md](CI_UPDATES.md) and [PAYLOAD_SYNC_FIX.md](PAYLOAD_SYNC_FIX.md) in this repo for related background and version-sync mechanics.
