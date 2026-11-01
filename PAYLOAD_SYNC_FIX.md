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
