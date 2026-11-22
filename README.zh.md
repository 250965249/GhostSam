# GhostSam

**中文** | [English](README.md)

面向三星 Galaxy S22 / S26 / Z Fold8 的内核提权工具包（Android 12 及以上，
内核 5.10-6.18），附带一个通过 ADB 驱动全流程的 Android 管理端 App。

源码树包含三条按机型划分的 Ghostlock 漏洞利用链（竞争性利用）、一条按内核
版本匹配的 dirtyfrag 安装链、一个共享的用户态层，以及负责打包预构建 payload
的管理端 App。

## 概览

- **目标机型**：Galaxy S22（`s22`）、Galaxy S26（`s26`）、Galaxy Z Fold8
  （`zf8`），覆盖 Snapdragon 与 Exynos 平台。
- **安装路径**：
  - **dirtyfrag（推荐，更快）**：单次运行即完成——经由 xfrm-ESP 页缓存
    4 字节写（CVE-2026-43284）部署 KernelSU 内核模块并直接 late-load，无需
    赢下竞争。
  - **Ghostlock 链**：面向所选系列的竞争性利用；运行过程中设备会重启，之后
    由 App 完成 KernelSU late-load。
- **内存破坏原语（CVE-2026-43499）**：把一个 futex PI 释放后使用（UAF）竞争
  转化为一次受控的 8 字节内核写；以 `misc` / `simple_attr` 文件描述符作为写
  载体，进而构建任意内核读写。Z Fold8 链使用基于 `io_submit` 的写路径。
- **root 阶段**：在 `system_unbound_wq` 上伪造一个 `work_struct`（函数为
  `call_usermodehelper_exec_work`），由内核以 init 身份启动我们的 helper；
  不修改凭证结构体，KDP 的 EL2 守卫无从触发。
- **root 通道**：`su_daemon` 在 `/data/local/tmp/temp_su.sock` 上维持一个
  root shell，贯穿整个会话。
- **持久化（临时 root）**：late-load KernelSU 或 KernelSU-Next，root 保持到
  下次重启为止（重启后重新跑链即可恢复）；Ghostlock 链中 `ksud` 以 bind
  mount 覆盖一个休眠的系统二进制，使基于路径的 DEFEX 规则看到的始终是白
  名单目标。

## 支持的机型

### Ghostlock 链（按机型匹配）

固件按内核基线匹配，而非逐 build 硬编码。每个系列携带一张生成的表：每个内核
基线一条偏移记录（17-18 个符号偏移），每个已知 build 一行；运行期按
series / codename / model / build 四个维度匹配，未收录的组合一律 fail-closed
（不发起任何内核写）。

| 系列    | 基线数 | 已知 build 数 | 机型 / 备注                                              |
| ------- | ------ | ------------- | -------------------------------------------------------- |
| s22     | 4      | 66            | SM-S9010 / S9060 / S9080 ...（骁龙、Exynos 2200）        |
| s26     | 4      | 153           | SM-S9420 / S9470 / S9480 ...（骁龙、Exynos），One UI 8.5 |
| s26-ui9 | 2      | 6             | One UI 9 布局，由 io_submit 链执行                       |
| zf8     | 6      | 73            | SM-F9710 / F9760 ...（Z Fold8）                          |

未收录固件可在运行期通过 `/data/local/tmp/ghostsam-lines.conf` 提供自定义
基线；格式由 `exploit/src/common/params_custom.h` 校验。

### dirtyfrag 链（按内核版本匹配）

dirtyfrag 链为每个 KMI 提供一个内核模块（App 资产中的 `dfr_lkm-<kmi>.ko`），
App 按设备内核选择对应模块。随包支持的 KMI：

| KMI            | 状态     | 备注                            |
| -------------- | -------- | ------------------------------- |
| android12-5.10 | 已测试   |                                 |
| android13-5.10 | 未测试   |                                 |
| android13-5.15 | 已测试   |                                 |
| android14-5.15 | 未测试   |                                 |
| android14-6.1  | 部分支持 | 仅 ≤6.1.71 内核；6.1.72+ 不支持 |
| android15-6.6  | 已测试   |                                 |
| android16-6.12 | 已测试   |                                 |
| android17-6.18 | 未测试   |                                 |

## 仓库结构

```
app/                             Android 管理端 App（Kotlin / Jetpack Compose）
  app/src/main/assets/payloads/  预构建 payload，随 APK 一起打包
exploit/
  src/common/                    共享层：日志、选项、参数表、KernelSnitch
                                 探测、KSU 协议、su_daemon、boot log、
                                 利用链骨架
  src/chains/pselect/            pselect 写链 + attr 载体 + UMH root 阶段
  src/chains/iosubmit/           io_submit 写链（zf8、One UI 9 的 s26）
  src/chains/dirtyfrag/          xfrm-ESP 页缓存存储安装链 + LKM 装载器
  src/series/s22|s26|zf8/        各系列的入口、参数表与汇编
```

## 构建

### App

所有 payload 已预构建于 `app/app/src/main/assets/payloads/` 下，APK 可独立
构建：

```bash
cd app
./gradlew assembleRelease
```

Gradle 任务 `:app:verifyPayloads` 会在构建时校验 payload 集合：每个系列目录
必须提供 `preload.so` 与 `su_daemon`，`payloads/ksud/` 必须提供 `ksud` 与
`ksud-next`，`payloads/dirtyfrag/` 必须提供 `dfr_payload` 及每个受支持 KMI
对应的一个 `dfr_lkm-<kmi>.ko`。

### 原生 payload

构建原生侧需要 Android NDK r30（或带 aarch64 sysroot 的宿主机 clang）。
`exploit/Makefile` 是唯一构建入口，产物落在 `exploit/build/`：

```bash
cd exploit
make            # 全量：各系列 + dirtyfrag payload
make s26        # 或单个目标：s26 / s22 / zf8 / dirtyfrag
```

### dirtyfrag 内核模块

该 LKM 是内核外部构建（out-of-tree kbuild）目标，需要一个准备好的内核构建
目录；Android DDK 容器会为每个 KMI 提供一份：

```bash
make lkm KDIR=/path/to/kernel-build-dir
```

### ksud 二进制

`payloads/ksud/ksud` 与 `payloads/ksud/ksud-next` 是预构建的 release 版本
（aarch64-linux-android），来自我们的 KernelSU fork：
[snothin/KernelSU](https://github.com/snothin/KernelSU) 与
[snothin/KernelSU-Next](https://github.com/snothin/KernelSU-Next)；版本升级
时从这两个仓库重新构建。

## 使用

1. 在受支持的设备上安装 APK（或按上文自行构建）。
2. 打开 USB 调试，通过 ADB 连接。
3. 在 App 内选择安装路径：
   - **dirtyfrag（推荐）**：单次运行——链直接完成 KernelSU 模块的部署与
     late-load。
   - **Ghostlock 链**：面向所选系列的竞争性利用；运行过程中设备会重启，
     之后由 App 执行 KernelSU late-load。

## 运行期开关

payload 读取少量由 App 经 ADB 注入的 `GHOSTSAM_*` 环境变量（例如
`GHOSTSAM_NO_KSU`、`GHOSTSAM_KSU_DEFER`、`GHOSTSAM_PARAMS_STRICT`）。权威
清单与语义以 payload 源码为准（`exploit/src/common/options.*`）；App 的 UI
仅暴露安全子集。

## 与 DFRoot 的关系

[DFRoot](https://github.com/diabl0w/DFRoot)（@diabl0w）是公开 DirtyFrag 技术
的专注、极简实现。GhostSam 把之前的工作（S22 / S26 / Z Fold8 的 Ghostlock
利用链）集成进同一个工具集，因此整个项目更大、也更为复杂——但 dirtyfrag
路径依然迅速：单次运行即完成部署与 late-load。欢迎体验，也欢迎提 Issue 或
PR。

两者基于同一公开技术；来源声明见 [NOTICE](NOTICE)。

## 免责声明

本仓库仅用于安全研究与教育，面向你拥有并控制的设备。运行 Ghostlock 链会
重启设备，会话期间 SELinux 可能处于 permissive（Knox watchdog 可能强制
重启）；强行指定错误的内核基线可能导致设备永久变砖。请自行对设备与当地
法律负责。

## 致谢与来源

- **s26 系列**：合并自 [ghostlock-s26](https://github.com/snothin/ghostlock-s26)，
  其 README 载有完整致谢清单。
- **Z Fold8 移植输入（io_submit primer）**：来自 @diabl0w。
- **ksud / ksud-next**：构建自 [snothin/KernelSU](https://github.com/snothin/KernelSU)
  与 [snothin/KernelSU-Next](https://github.com/snothin/KernelSU-Next)；这两个
  fork 跟踪上游并吸收 KernelSU 社区多位开发者的成果。
- **dirtyfrag 链**：沿袭公开的 DirtyFrag 技术；来源声明见 [NOTICE](NOTICE)。

## 许可

Apache-2.0，见 [`LICENSE`](LICENSE)。第三方声明与致谢见 [`NOTICE`](NOTICE)。

---

## 本 Fork 的构建体系与维护（250965249 维护）

> 本节描述本 fork 自建的三条三星适配 producer 链与 GhostSam 消费端的完整体系，面向长线维护，自包含可独立阅读。上文为上游 snothin 项目说明，保持原样。

### 1. 四仓库架构与每日时序

本 fork 把「三星适配内核构建」与「APK 出包」彻底拆成 producer / consumer 两层：三个 producer 各自把官方上游合入三星补丁并产出可复用 payload，GhostSam 只负责消费与打包。

| 角色 | 仓库 | 分支 | 同步上游（基线示例） | 当前基线示例 |
| --- | --- | --- | --- | --- |
| 消费端 | `250965249/GhostSam` | `bakasu` | — | 消费三变体产物出 APK |
| producer | `250965249/KernelSU-Next` | `samsung` | `KernelSU-Next/KernelSU-Next@dev`（UAPI 6） | variant=`next` |
| producer | `250965249/KernelSU` | `samsung` | `tiann/KernelSU@main`（UAPI 6） | variant=`default` |
| producer | `250965249/SukiSU-Ultra` | `samsung` | `SukiSU-Nova/SukiSU-Nova@main` | variant=`sukisu` |

> 表中上游 ref 为稳定示例基线；具体 HEAD、版本号、run id 一律**以仓库当前状态为准**（这类值变化快，不在文档写死）。

每日时序（Asia/Shanghai）：

- **02:00**：三个 producer 各自 `auto-sync-build` —— merge 各自官方上游 → 8-KMI 双架构 DDK LKM + ksuinit + aarch64 ksud → 发布 `latest-ksud` release（`ksud-<variant>-aarch64` + `.version` sidecar）。
- **05:00 / 09:00 / 13:00**：GhostSam `auto-build` 拉三变体 + sidecar → 重建 `versions.properties` / `payloads.lock.json` → 打 APK → 发布 `auto-<时间戳>` release（附 KernelSU / KernelSU-Next / SukiSU 三个官方管理器 APK）。
- 两层解耦：producer 只发 payload，GhostSam 只消费；任一变体缺 sidecar、落后上游或版本戳与树不符，出包后尾部闸门报红，绝不无声发旧包。

### 2. 三条下游分支详解

| variant | 基线（上游） | 宏集（构建行红线） | SAMSUNG_OWNED | 备注 |
| --- | --- | --- | --- | --- |
| `next` | 官方 dev（UAPI 6） | `CONFIG_KSU_SAMSUNG_KDP / RKP / DEFEX / NO_PATCH_TEXT`（四宏） | 17 文件（11 kernel + 6 ksud late-load） | 重建自官方 dev + snothin 三星补丁集 |
| `default` | tiann main（UAPI 6） | `CONFIG_KSU_RKP_NO_PATCH_TEXT / CONFIG_KSU_SAMSUNG_KDP / CONFIG_KSU_SAMSUNG_DEFEX`（三宏，宏名与另两树不同） | 21 文件 | LKM 双命名 `{kmi}_kernelsu.ko` + `{kmi}-exynos_kernelsu.ko`，供 late_load 的 `KSUD_TREE=exynos` 与 boot patch 两路解析 |
| `sukisu` | SukiSU-Nova main | 四宏（同 `next`） | 见 producer CI | `selinux_hide` 五点 kprobe |

- **宏名是红线**：原版树的 NO_PATCH_TEXT 写作 `CONFIG_KSU_RKP_NO_PATCH_TEXT`（非 `SAMSUNG_` 前缀），且无独立 `CONFIG_KSU_SAMSUNG_RKP` 宏；ddk 构建行与 `kernel/Kbuild` 门控必须用各自那套名字，照抄另两树会静默跳过 `samsung_rkp.c`，导致管理器报「未安装」。
- `next` / `default` 必须满足 GhostSam 的 samsung-ci 全套契约；`sukisu` 走 release 特例，真机回归见第 5 节。

### 3. 三星适配方法论

- **Knox 三防御**：KDP（`cred` 等敏感结构所在内存受 EL2 保护，须用三星安全内存/只读穿透接口）、RKP（监控内核 text 与页表，严禁运行时改只读代码段，违者 EL2 直接 Panic 死机重启）、DEFEX（内核白名单阻止直接执行 `/data` 下的可执行文件/模块，须 staging 到 `/dev` 或 memfd）。三者分别对应 KDP / RKP / DEFEX / NO_PATCH_TEXT 宏。
- **`NO_PATCH_TEXT` 铁律**：三星 RKP 下所有 `ksu_patch_text` / `ksu_lsm_hook` 面恒返回 `-EOPNOTSUPP`。每一处补丁点必须找到 kprobe 替代，或显式禁用并文档化（如 `mount_hide` 维持禁用，与历史基线同等状态）。
- **selinux_hide 五点**：`context` / `access` / `setprocattr` / `status` / `inode_setxattr` 各配一个 `samsung_*_kprobe`。**单点失败降级原则**：任一点 kprobe 注册失败只 `pr_err` 告警并降级该点，绝不整体 `unhook` —— 2026-11 事故教训：上游新增 `inode_setxattr` 隐身面而三星侧无通道，最后一步失败曾把已装好的四点一并拆除，导致 SELinux hide 全失效、被检测工具命中。
- **init.c 顺序**：`ksu_init_symbol_resolver()` → `ksu_samsung_kdp_init()` → `prepare_creds()`（失败回滚）→ `ksu_samsung_defex_init()` → 上游新增 init 调用（如 `ksu_selinux_inode_init` / `ksu_preinit_init`）；exit 对称。
- **LKM 编译期嵌入**：内核模块在编译 `ksud` 时由 RustEmbed/`include_bytes!` 打包进二进制，更新 LKM 必须重编 `ksud`，不能只替换 `.ko`。
- **回归原则**：上游每次新增隐身面（新 `ksu_patch_text` / `ksu_lsm_hook` 调用点）都必须补三星通道或显式决策，并记入审计表。

### 4. 维护操作速查

版本公式（**动态 grep `userspace/ksud/build.rs`，禁止硬编码**，历史上游多次改过）：

| variant | 公式 | 说明 |
| --- | --- | --- |
| `next` | `VERSION_CODE = 30000 + git rev-list --count HEAD` | |
| `default` | `VERSION_CODE = 30000 + git rev-list --count HEAD` | |
| `sukisu` | `VERSION_CODE = 30996 + git rev-list --count HEAD` | build.rs 中即 `30000 + 996` |

- **版本权威**是二进制 `.rodata` 里的 `KSU_NO_NEW_PRIVS<5位code><name>` 戳；`versions.properties` 由探针回读生成并与 sidecar 交叉校验，不一致即报错。
- **sidecar 契约要点**：`.version` 含 `build_kind=samsung-ci`、`kernel_version_code`、`kernel_uapi_version`、`official_ci_commit`（官方 CI commit，40 位，用于锁定官方管理器）、`producer_run_id`、`producer_artifact_id`、`producer_artifact_digest`，以及 `behind_upstream`；内核 UAPI 必须与官方管理器相等。
- **冲突处理**：`SAMSUNG_OWNED` 白名单内自动 `git merge -X ours`；白名单外报红；若 `merge_auto_resolved` 含 `kernel/` 路径，GhostSam 拒收当天该变体产物（安全设计，kernel 冲突不许静默），需人工三方合并后重 dispatch。
- **fork schedule 通病**：Actions 权限 enabled ≠ schedule 已激活；fork 的定时 workflow 常在 Actions 页需 owner 点一次 Enable 横幅，否则 cron 静默不跑。
- **已知坑位**：`GITHUB_TOKEN` 无 workflows 权限，不能推 tag/workflow；`git fetch --tags` 会覆盖 refspec 重新导入非 v tag（污染 `git describe --tags`）；`actions/checkout` 必须 `ref: ${{ github.ref_name }}` 而非 `github.sha`（否则编译上一轮树）；`gh release upload --clobber` 有 422 竞态，先删后传；`find … \| head` 在 `pipefail` 下 SIGPIPE 被误判失败，用 `-print -quit`。

### 5. 回归清单与案例

每次上游同步后核对：sidecar `behind_upstream=0`、二进制版本戳与树一致、LKM 产物含三星字符串（KDP/DEFEX/RKP sucompat/selinux_hide）、`selinux_hide` 五点产物级验证（5 个 `samsung_*_kprobe` 与 registered 标志真实编入）。

**案例（41179，2026-11）**：上游 `5b6262e8` 给 `selinux_inode_setxattr` 新增第五个伪装点，而三星 fork 只覆盖四点；第五点仍走 `ksu_lsm_hook → ksu_patch_text`，在 `NO_PATCH_TEXT` 下恒失败，触发 `ksu_selinux_hide_enable()` 整体 `unhook`，SELinux hide 全失效、被检测工具命中（LSPosed / SELinux 泄露）。修复 = 补 `inode_setxattr` 的 RKP kprobe 通道 + 把单点失败改为非致命降级，并修 CI 的 `manager-ci` tag 污染 `git describe`。相关背景与版本同步机制另见仓库内 [CI_UPDATES.md](CI_UPDATES.md) 与 [PAYLOAD_SYNC_FIX.md](PAYLOAD_SYNC_FIX.md)。
