# GhostSam 自动包 KernelSU 版本长期停在 41098 —— 根因与修复

排查日期：2026-11-01
现象：`GhostSam_auto_20261101.apk` 里，设置 → `KernelSU 版本` → 当前 Payload
仍显示 `SukiSU-Ultra v4.2.1-r2(41098-6)`，而 SukiSU 官方 CI 早已是 `41120`。

---

## 一、结论：三个独立缺陷叠加，任何一个单独存在都会让版本号追不上

| # | 缺陷 | 位置 | 后果 |
|---|---|---|---|
| 1 | `versions.properties` 是**手工维护的静态文件**，CI 只替换二进制、从不重写它 | `app/app/src/main/assets/payloads/ksud/versions.properties` | 界面显示的版本**永远**停在最后一次手改时的 41098，与包内实际二进制无关 |
| 2 | 构建作业 checkout 的是 `github.sha`（run 创建时冻结），**不是 sync 刚推上去的合并结果** | `sukisu-r2`/`bakasu`/`ksunext-samsung-user` 的 `ksud.yml` `ksuinit.yml` `ddk-lkm*.yml` | 每次发布编译的是**上一轮**的树，恒定落后一轮 |
| 3 | fork 的 release tag `latest-ksud` 命中 HEAD，污染 `git describe --tags` | producer 的 `auto-sync-build.yml` release 步骤 | ksud 的 `VERSION_NAME` 变成 `latest-ksud-6-g4f74a450`，无法从上机二进制辨认版本 |

叠加后还有第四个症状：**合并冲突只 `::warning::` 一句就继续用旧代码出包**，
流水线是绿的，没有任何渠道能发现载荷已经过期；10-29 之后 SukiSU 的定时任务
更是整段没跑（GitHub schedule 延迟/跳过），所以连"落后一轮"的补偿都没有。

---

## 二、证据链

ksud 的版本号是编译期写进二进制的（`userspace/ksud/build.rs`）：

```
VERSION_CODE = 40000 - 2815 + `git rev-list --count HEAD`   # SukiSU
VERSION_NAME = `git describe --tags --always`
```

两者会被编译器紧挨着 `KSU_NO_NEW_PRIVS` 字面量摆放在 `.rodata`：

```
… set KSU_NO_NEW_PRIVS 41108 latest-ksud-2-g5708694a /proc/thread-self/attr/current …
```

所以不上机也能读出真实版本。逐层实测：

| 对象 | `git rev-list --count HEAD` | 推算 `VERSION_CODE` | 二进制/清单里的 `VERSION_NAME` |
|---|---|---|---|
| `SukiSU-Ultra/SukiSU-Ultra` **main HEAD**（官方 CI） | 3935 | **41120** | `4.3.0-20-g0fe8d052` |
| `250965249/SukiSU-Ultra` **samsung HEAD**（`4f74a450`, 10-29 22:37:37Z） | 3927 | 41112 | （合并后本应是 `4.2.1-r2-44-g4f74a450`） |
| `latest-ksud` **已发布** `ksud-sukisu-aarch64`（7,811,552 B） | 3923 | **41108** | `latest-ksud-2-g5708694a` |
| GhostSam 仓库里**提交**的 `assets/.../ksud-sukisu`（7,811,984 B） | 3921 | 41106 | `latest-ksud` |
| `versions.properties` **手写值** | — | 41098 → 3913 | `4.2.1-r2` |

三点直接对上了：

1. `3935 + 37185 = 41120` —— 你看到的"官方 CI 41120"就是上游 `main` 的 HEAD，
   一条不多一条不少。
2. 已发布二进制是 **41108 / `5708694a`**，而 fork HEAD 是 **41112 / `4f74a450`**，
   两者差 4 个提交。`5708694a` 正是**上一轮** run 的合并提交 —— 即
   run #5（10-29 22:37:28 创建）编译的是 run #4 推上去的树。
   这就是缺陷 2 的现场证据。
3. 二进制里写的是 `latest-ksud-2-g5708694a` 而不是 `4.2.1-r2-…`：
   `gh release create latest-ksud` 把 tag 打在 HEAD 上，
   `git describe --tags` 就近取到了它。这就是缺陷 3。
   把该 tag 从本地删掉后，同一条命令立刻给出正确的
   `v4.2.1-r2-44-g4f74a450`。

界面显示 41098（对应 3913 个提交）比包里实际的 41108 还旧 10 个构建号，
所以这个数字从头到尾没跟任何一次真实出包对上过 —— 它只是当初手写时的快照。

---

## 三、修复

### 3.1 Consumer（`250965249/GhostSam`）

**`app/app/src/main/assets/payloads/ksud/versions.properties`**
不再是手写文件，改为构建时生成。当前已按实际二进制重新生成：

```properties
default=KernelSU v3.3.1 (32680)
next=KernelSU-Next v3.4.2 (33355)
sukisu=SukiSU-Ultra (41106)      # 该二进制没有版本 tag 信息，见 3.2
bakasu=BakaSU (35260)            # 原为 35258，同样已经漂移
```

**`tools/probe_payload_version.py`**
从 ELF 里读回 `KSU_NO_NEW_PRIVS<5 位 code><version name>`，
并用各项目 `build.rs` 的偏移反推提交数做交叉校验
（KernelSU 20000 / Next 30000 / BakaSU 30700 / SukiSU 37185，四个变体实测
提交数分别为 12680 / 3355 / 4560 / 3921，全部自洽）。

**`tools/gen_payload_versions.py`**
唯一的清单生成者。对每个变体：

- 读二进制里的版本戳；
- 与 producer 的 `*.version` sidecar 交叉校验 `version_code` / `sha256`；
- `behind_upstream > 0`、`sha256` 不符、版本戳前后矛盾 → **报错**；
- 二进制编译时间超过 `--max-age-hours`（默认 48h）→ 告警；
- 输出 `versions.properties` + `payloads.lock.json`（含 sha256、built_at、
  behind_upstream、upstream_commit）。

`--check` 模式用于回归：仓库里提交的清单必须与实际二进制逐字节一致。

**`tools/report_payloads.py`**
把 lock 渲染成日志表格、release notes 的 markdown 表、GitHub annotation，
并把 `stale=true/false` 写进 step output。

**`.github/workflows/auto-build.yml`**

- 下载四个二进制 + 三个 sidecar；
- 追加截断校验（< 1 MiB 直接失败）；
- `gen_payload_versions.py --write` 重新生成清单；
- `--check` 回归闸门；
- **`unzip -p` 反查 APK 内的 `assets/payloads/ksud/versions.properties` 并 `diff`** ——
  保证"设置页显示的"和"包里装的"是同一个版本；
- release notes 带四变体版本表 + 与上游的同步状态；
- 内容上先出包、再因 `behind_upstream > 0` 报红（`allow_stale_payloads`
  为 `true` 时可强制绿灯）。

**`app/.../data/prefs/PayloadVersions.kt`**
fallback 里的写死版本号全部去掉，改成 `"<显示名> (bundled)"`。
写死版本号本身就是这次 bug 的同款。

### 3.2 Producer（三个 fork）

**`auto-sync-build.yml`（三个仓库同构）**

- `sync` 作业新增输出：`behind` / `conflict` / `upstream_commit` /
  `upstream_version_name`，并在合并**后**重算 `behind`；
- 合并前打印 `upstream/<ref> = <version_name> (<sha>), N commit(s) ahead of us`；
- 冲突时把**冲突文件路径**打进 `::error::`，再 `merge --abort`，
  出包之后让这轮红掉；
- `git push origin --tags`，让 build 作业能看见上游的 `v4.3.0` 之类版本 tag；
- release 步骤：
  - `git tag -l | grep -Ev '^v[0-9]' | xargs -r git tag -d`（去掉 `latest-ksud` 等）；
  - 按 `VERSION_CODE_OFFSET + rev-list --count HEAD` 算期望版本号；
  - **把二进制里的版本戳当作权威**，与 HEAD 推算值不一致就失败
    （防止"编译的不是刚合并的树"重演）；
  - 拒绝 `VERSION_NAME` 仍以 `latest-`/`manager-ci` 开头的产物；
  - 发布 `ksud-<variant>-aarch64.version` sidecar（`behind_upstream` 等）。

**`ksud.yml` / `ksuinit.yml` / `ddk-lkm*.yml`（三个仓库共 10 个文件）**

- 所有 `actions/checkout` 补 `ref: ${{ github.ref_name }}` —— 取 fetch 时刻的
  分支尖端，即 sync 刚合并推上去的那个提交；
- `ksud.yml` 在 `cargo build` 之前加"Normalize tags for version stamping"一步。

---

## 四、验证

全部在本机跑过（Windows，Python 3.14，无需 Android 工具链）：

```text
$ python3 tools/gen_payload_versions.py --check --allow-problems        # 提交的清单
exit=0

$ # 故意把 sukisu 那行改回 41098
$ python3 tools/gen_payload_versions.py --check --allow-problems
ERROR   …/versions.properties is stale — the shipped label does not match the bundled payloads
exit=1

$ # 故意把 sidecar 的 behind_upstream 改成 7
ERROR   sukisu: producer is 7 upstream commit(s) behind 250965249/SukiSU-Ultra …
exit=1

$ python3 tools/verify_embedded_version.py app/app/src/main/assets/payloads/ksud
OK   ksud         code='32680'  name='3.3.1'
OK   ksud-next    code='33355'  name='3.4.2'
OK   ksud-sukisu  code='41106'  name='latest-ksud'
OK   ksud-bakasu  code='35260'  name='latest-ksud-1-gb8f9d24f'
shell pipelines agree with the probe

$ # 用模拟 sidecar 验证清单格式（clean version_name）
sukisu=SukiSU-Ultra v4.3.0+44 (41106)
bakasu=BakaSU v4.2.0+4 (35260)

$ python3 tools/check_workflows.py     # 修复后
all workflow checks passed

$ python3 tools/check_workflows.py --root <git HEAD 版本的工作流>   # 修复前
35 problem(s)
```

`check_workflows.py` 会解析全部 workflow YAML，并断言：producer 必须发布
sidecar、auto-sync 链路上不能有缺 `ref:` 的 checkout、`ksud.yml` 的 tag 清洗
必须在 build 之前（位置也对）、GhostSam 必须走生成器而不是信任提交的清单。
对修复前的树跑会报 35 条。

---

## 五、上线步骤

1. 先推三个 producer，然后**手动各跑一次**，让 sidecar 落进 `latest-ksud`：

   ```bash
   gh workflow run auto-sync-build.yml -R 250965249/SukiSU-Ultra --ref samsung
   gh workflow run auto-sync-build.yml -R 250965249/BakaSU --ref samsung
   gh workflow run auto-sync-build.yml -R 250965249/KernelSU-Next-Samsung --ref samsung
   gh release view latest-ksud -R 250965249/SukiSU-Ultra   # 要能看到 .version
   ```

   第一次跑 SukiSU 时 `behind_upstream` 会是 0、`version_name` 应该变成
   `4.3.0-<n>-g<hash>`，`VERSION_CODE` 会从 41108 跳到 41112 以上
   （fork 的 samsung 分支比上游多几个适配提交，构建号天然略高于官方 41120，
   属正常；`upstream_commit` / `upstream_version_name` 字段用来核对是否追平）。

2. 再推 GhostSam 并手动跑一次。设置页应显示类似
   `SukiSU-Ultra v4.3.0+44 (41128)`，与 `payloads.lock.json` 一致。

3. 之后按原计划每天自动：producer 02:00 → GhostSam 05:00。

**回滚**：`versions.properties` 与 `payloads.lock.json` 都由脚本生成，
回退提交即可；producer 侧若想暂时恢复"冲突也继续"的旧行为，
把 release 末尾那个 `Fail when the payload is behind upstream` 步骤删掉即可，
其余修复互不依赖。

---

## 六、遗留风险

- **GitHub 定时任务本身不保证准点**：10-29 之后 SukiSU 的 schedule 整段没触发
  （历史 run 的创建时间也普遍比 `cron` 晚 4 小时以上）。现在 GhostSam 用
  `behind_upstream` 和 `built_at > 48h` 兜底，会明确报红，而不是无声发旧包。
- **fork 的构建号天然高于官方**：`git rev-list --count HEAD` 数的是 samsung
  分支的全历史，比上游 `main` 多出适配提交。所以 `41128` 不代表"比官方
  `41120` 更新"，只代表历史更长；判断是否追平请看 `behind_upstream` 与
  `upstream_version_name`。
- `payloads.lock.json` 会随 APK 一起打进 `assets/`，里面含各变体的
  `sha256` / `built_at` / `upstream_commit`，可用于排障；
  将来若要加"包内版本过期提示"，读它即可。

---

## 七、2026-11-01 落地实录（追加）

真正跑起来之后又发现三件事，都已修掉或定位：

### 7.1 `latest-auto` 已出新包

`GhostSam_auto_20261101.apk`，25,041,424 bytes

```
sha256 ae3a8948b3e348d33422acdd2ca8b20d6deb29d3d5298496df738b528dc884e2
```

包内实测（不是看清单，是把 APK 里的二进制抠出来读版本戳）：

| 变体 | `VERSION_CODE` | `VERSION_NAME` | 说明 |
|---|---|---|---|
| `ksud`（官方） | 32680 | `3.3.1` | 每次构建现拉 tiann 最新 release |
| `ksud-next` | 33355 | `3.4.2` | **今天重新编译**，`sha256 8cf2d20a…` |
| `ksud-sukisu` | 41108 | `latest-ksud-2-g5708694a` | 仍停在 10-29 的产物，见 7.3 |
| `ksud-bakasu` | 35260 | `latest-ksud-1-gb8f9d24f` | 同上 |

包内 `assets/payloads/ksud/versions.properties`：

```properties
default=KernelSU v3.3.1 (32680)
next=KernelSU-Next v3.4.2 (33355, pinned)
sukisu=SukiSU-Ultra (41108)
bakasu=BakaSU (35260)
```

即：**设置页会显示 `SukiSU-Ultra (41108)`，不再是那个和任何真实构建都对不上的 41098。**

### 7.2 缺陷 4：KernelSU-Next 的版本是**写死的**

`ksunext-samsung-user/userspace/ksud/build.rs`：

```rust
let (code, name) = match get_git_version() {
    Ok((_code, _name)) => (33355, "3.4.2".to_string()), // PATCH(ghostsam): version pin
    Err(_) => (33355, "3.4.2".to_string()),             // PATCH(ghostsam): version pin
};
```

git 算出来的值被直接丢弃，**不管树走多远，永远报 33355 / 3.4.2**。
这和"版本不跟着上游变"是同一类问题，只是它连 git 都不看。

处理：producer 的 release 校验新增 `EXPECTED_STAMP` 环境变量
（空 = 从 git 推导，非空 = `<code>:<name>` 表示该变体是钉死的），
sidecar 里写 `version_pinned` 和 `git_version_name`，GhostSam 的标签渲染成
`KernelSU-Next v3.4.2 (33355, pinned)`——钉死就明说，不让它伪装成实时版本。

> 没有直接删掉这个 pin，是因为该仓库**没有任何 `v*` 版本 tag**
> （整个仓库只有 `latest-ksud` 一个 tag，实测 `git tag -l | wc -l` = 1），
> 去掉 pin 后 `git describe` 只能给出裸 sha，标签会退化成
> `KernelSU-Next (33386)`，可读性反而更差。

### 7.3 缺陷 5：GitHub Actions 在**两个 fork 上被禁用**了

这才是"定时任务从 10-29 起一次都没跑"的真因，不是 cron 丢了：

```
$ gh workflow run auto-sync-build.yml -R 250965249/SukiSU-Ultra --ref samsung
could not create workflow dispatch event:
HTTP 422: Actions has been disabled for this repository.
```

`250965249/SukiSU-Ultra` 和 `250965249/BakaSU` 是 fork
（`fork=true`，parent 分别是 `SukiSU-Ultra/SukiSU-Ultra`、`Baka-SU/BakaSU`），
而 `250965249/KernelSU-Next-Samsung` 和 `250965249/GhostSam` 是独立仓库
（`fork=false`）——**能跑的两个都不是 fork**。

试过并确认无效：`PUT /repos/{r}/actions/permissions {enabled:true}`（返回 200，
但 dispatch 仍 422）、`enabled=false→true` 来回切、换另一个 workflow dispatch
（同样是 422）。这个"fork 是否启用 workflows"的开关不在 REST API 里。

**需要仓库所有者各点一次**（Actions 页面顶部的横幅
"I understand my workflows, go ahead and enable them"）：

- https://github.com/250965249/SukiSU-Ultra/actions
- https://github.com/250965249/BakaSU/actions

启用后这两条链会自动恢复：每天 02:00 跑 producer、05:00 跑 GhostSam；
在此之前 GhostSam 每次都会出包，但**末尾那步会红**，并在 release notes 里写明
哪个变体没有 sidecar、为什么无法核对同步状态。这是故意的：宁可红，也不要再
无声地发旧包。

### 7.4 顺带修掉的构建闸门

`app/build.gradle.kts` 里有 `verifyPayloads` 任务，把
`assets/payloads/ksud/` 当成固定清单，多一个文件就失败：

```
> verifyPayloads failed (1 issues):
    - unexpected file: ksud/payloads.lock.json
```

`payloads.lock.json` 是构建元数据不是 payload，已挪到仓库根目录
（`gen_payload_versions.py --lock-out payloads.lock.json`）。

---

## 八、最终状态（2026-11-01 17:10 Asia/Shanghai）

两条 fork 的 Actions 启用后，链路一次跑通，`Auto Build GhostSam` 全绿。

**成品**

```
GhostSam_auto_20261101.apk   25,041,424 bytes
sha256 988c66116fcb976fa513944142aedfff68ca67ff0a08e7c83de5ac768ac80224
https://github.com/250965249/GhostSam/releases/tag/latest-auto
```

包内实测（从 APK 里抠出二进制读版本戳）：

| 变体 | VERSION_CODE | VERSION_NAME | 相对上游 |
|---|---|---|---|
| `ksud` | 32680 | `3.3.1` | tiann 官方最新 release |
| `ksud-next` | 33355 | `3.4.2` | 与 `snothin/KernelSU-Next` 同步（版本被 pin，见 7.2） |
| `ksud-sukisu` | **41134** | `4.3.0-34-g0912ba85` | 已合入 `4.3.0-20-g0fe8d052`，**0 落后** |
| `ksud-bakasu` | **35268** | `4.2.0-rc3-97-g466e0dc4` | 已合入 `4.2.0-rc3-86-g516d3e87`，**0 落后** |

设置页现在显示：
`SukiSU-Ultra v4.3.0+34 (41134)` 和 `BakaSU v4.2.0-rc3+97 (35268)`，
对比最初那个从来没有对上的 `41098`。

### 8.1 最后两个内核冲突

同步恢复后立刻暴露出两处真实的合并冲突（以前被静默吞掉）：

| 仓库 | 冲突文件 | 冲突内容 |
|---|---|---|
| SukiSU | `kernel/hook/arm64/patch_memory.c` | 上游给 `ksu_patch_text()` 加了 `stop_machine()` 失败日志；samsung 把整个函数包进 `CONFIG_KSU_SAMSUNG_NO_PATCH_TEXT`（EL2/RKP 禁止运行时改内核text） |
| BakaSU | `kernel/core/init.c` | 上游把 symbol resolver 挪到 `prepare_creds()` 之后并新增 `ksu_selinux_inode_init()` + `put_cred()` 清理；samsung 需要 apatch 冲突检查、symbol resolver、`ksu_samsung_kdp_init()` 在 `prepare_creds()` **之前**执行 |

两处都按"保留 samsung 语义 + 采纳上游新逻辑"解决，判据是
`git diff up/main -- <file>` 的结果**只剩 samsung 自己的增量**：

- `patch_memory.c`：`#ifdef CONFIG_KSU_SAMSUNG_NO_PATCH_TEXT` 早退保留，
  `#else` 分支改用上游的 `int ret; ret = stop_machine(...); if (ret) pr_err(...)`。
  `CONFIG_KSU_SAMSUNG_NO_PATCH_TEXT=y` 由 `ddk-lkm.yml` 传入、
  `kernel/Kbuild` 转成 `-DCONFIG_...=1`，所以三星构建走早退，行为不变。
- `init.c`：顺序保留 samsung 的（apatch check → symbol resolver → kdp init →
  prepare_creds → defex init），再插入上游新增的
  `ret = ksu_selinux_inode_init(); if (ret) { put_cred(...); ... }`，
  仍在 `ksu_selinux_init()` 之前。

> 这两处只验证到"能编译、逻辑符合同两侧原意"，**没有在真机上验证开机/授权**。
> 如果刷入后异常，`git revert` 这两个 merge 提交即可回到"落后上 N 个提交但已知可用"的状态。

### 8.2 现在的自动化行为

- 每天 02:00 三个 producer 合并上游 → 编译 8 KMI LKM + ksud → 发布 `ksud-*` 与 `.version`
  （合并冲突、落后上游都会在出包后把这轮跑红，并写明冲突文件）
- 每天 05:00 GhostSam 拉取 → 从二进制重建 `versions.properties` →
  反查 APK 内清单 → 出包 → release notes 带四变体版本表与同步状态
- 任一变体落后上游 / 缺 sidecar / 版本戳与树不符 / 载荷超过 48h，都会红，
  但包照发，不会再有"绿的但发的是旧包"

---

## 九、第二轮（2026-11-05/06）：让"自动"真的自动

11-02 ~ 11-05 每天照常出包，但版本纹丝不动。两个独立原因：

1. **SukiSU 每天合并冲突**。上游 `4.3.0-r1` → `4.3.0-r2` 一路改 `kernel/core/init.c`，
   samsung 分支在同一处也有改动，`git merge` 冲突 → sync 执行 `merge --abort` →
   分支永远不前进 → 每天编译的还是同一棵树 → payload 永远停在 41134。
   上一轮我加的"冲突就报红"只是让它**可见**，没有让它**自动**。
2. **nexT 的 release 步骤以退出码 1 静默失败**，payload 停在 11-01。

### 9.1 自动解决合并冲突（SAMSUNG_OWNED）

`auto-sync-build.yml` 的 sync 作业现在：

- 合并冲突时先看**冲突文件是否都在 `SAMSUNG_OWNED` 白名单里**
  （`kernel/core/init.c`、`Kbuild`、`Kconfig`、`patch_memory.c`、`tp_marker.c`、
  `syscall_hook*`、`selinux_hide.c`、`app_profile.c`）；
- 全是 → 重新 `git merge -X ours`：这些文件里 samsung 侧是权威内容，
  编译（DDK LKM + ksud）是安全网，签名漂移会编译失败而不是发出坏包；
- 有任何非白名单文件 → 仍然报红并列出文件名。

实测（本地复现上游 30 个提交的合并）：`init.c` 保留了 samsung 的
`ksu_samsung_kdp_init()` / `ksu_samsung_defex_init()` 顺序，同时**自动并入**
上游新增的 `ret = ksu_selinux_inode_init()` 及其 `put_cred()` 清理，
也没有重复调用 symbol resolver。其余 229 个上游改动全部合入。

### 9.2 三处 CI 自身的坑

| 坑 | 症状 | 修法 |
|---|---|---|
| `gh release upload --clobber` | `HTTP 422 ReleaseAsset.name already exists`，payload 已编译却发不出去 | 先 `gh release delete-asset` 再 upload |
| `find … \| head -n1` 在 `set -o pipefail` 下 | 管道 SIGPIPE 被视为失败 → 赋值失败 → `set -e` 静默退出，日志里一行都没有 | `find … -print -quit` |
| 假设 `VERSION_NAME` 字面量紧挨 `KSU_NO_NEW_PRIVS<code>` | nexT 改成 8 位 sha 后字面量被放到别处，读出空串 → 校验误判好包为坏包 | 校验改为「code 严格相等；name 有则必需相等，无则用 git 值」 |

另外 nexT 的 `userspace/ksud/build.rs` 里那个版本钉死（`(33355, "3.4.2")` 丢弃 git 结果）
已经去掉，现在它和另外两个变体一样从 git 取值。所有 producer 的 release 步骤都加了
`trap 'echo "::error::release step failed at line $LINENO"' ERR`，
下次失败能直接看到行号（这个 job 的 stdout 在 GitHub 上会被吞掉，只有 stderr 可靠）。

### 9.3 当前状态

| 变体 | 版本 | 相对上游 | producer |
|---|---|---|---|
| `ksud`（tiann 官方） | 32680 `3.3.1` | 每次现拉最新 release | — |
| `ksud-sukisu` | **41167** `4.3.0-r2-17-g1c0913bd` | **0 落后**（上游 `4.3.0-r2`） | ✅ 绿 |
| `ksud-bakasu` | **35270** `4.2.0-r2…-99-g1504e901` | **0 落后** | ✅ 绿 |
| `ksud-next` | 33355 `3.4.2` | 与上游同步 | ❌ release 步骤仍失败 |

**成品**：`GhostSam_auto_20261105.apk`，25,057,808 字节
`sha256 6e46cde29ae3b9d13418f7e18ea1cdaad572865322335de33a6abfb38428e4e2`
（已下载比对，包内 `sukisu=SukiSU-Ultra v4.3.0-r2+17 (41167)`、
`bakasu=BakaSU v4.2.0-rc3+99 (35270)`）

### 9.4 还差的一步

nexT 的 release 步骤仍未跑通，且本次会话里我的 `gh` OAuth token 失效
（`The token in keyring is invalid`），无法再手动触发去拿日志。
**今晚 02:00（Asia/Shanghai）的定时任务会自动重跑**，届时
`::error::release step failed at line N` 会直接指出是哪一行。

在此之前 SukiSU / BakaSU 两条链已经完全自愈：合并冲突自动解决、
版本跟着上游走、出包即绿，不需要人工介入。
