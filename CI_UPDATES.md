# KernelSU / KernelSU-Next CI 来源与配套状态

## 本次核对到的事实

本记录使用官方仓库及 GitHub Actions 元数据，不用中间 fork 的 `behind_upstream=0` 代替官方同步状态。

| 项目 | 官方 CI 源码 | 最新成功非 PR 运行 | 最终管理器 artifact | UAPI |
| --- | --- | --- | --- | --- |
| KernelSU | `tiann/KernelSU@main`，`2a60b2893b8d0d885d1ceff2287d8ad854f0e0f4` | [44884831714](https://github.com/tiann/KernelSU/actions/runs/44884831714) | `manager`，ID `14700066027` | 6 |
| KernelSU-Next | `KernelSU-Next/KernelSU-Next@dev`，`32e25d58fc1a187e1abe7278904c31571235fc83` | [44888862619](https://github.com/KernelSU-Next/KernelSU-Next/actions/runs/44888862619) | `manager`，ID `14701785542` | 6 |

不能用 `manager-gradle` 替代 KernelSU 的最终 `manager`：前者是重打包前的中间件。不能用 `manager-spoofed` 替代 Next 的普通管理器。

现有适配源码的状态：

- [snothin/KernelSU@samsung](https://github.com/snothin/KernelSU/tree/b086147e79e2725177d960e7161ba279288efc9d) 的基线 UAPI 为 5，相对官方 CI 缺 55 个提交；它的 `kdp-s26` 分支则缺 111 个提交，UAPI 为 2。本次未查到该仓库可验证的成功 CI 生产产物或 sidecar。
- [250965249/KernelSU-Next-Samsung@samsung](https://github.com/250965249/KernelSU-Next-Samsung/tree/ae96ed3116c8b876933f0624183fb97f5de150e3) 仍为 UAPI 5，跟踪的是 `snothin/KernelSU-Next@samsung`，相对真正官方 `dev` 缺 200 个提交。sidecar 的 33404 是 ksud 版本；该源码的 Kbuild 仍固定内核版本 33355，二者不能混称。
- Next 官方 v3.4.2 的 UAPI 头文件与现有三星基线相同，是当前可核查的旧版接口基线；最新 dev 的 UAPI 6 不同。相同头文件或相同签名仍不等于真机兼容性验证。

这些计数是本次查询的源码快照，不会永久成立；脚本每次重新查询。提交祖先关系也不能证明 merge 冲突没有覆盖某项功能修复。

## 已新增的抓取与检查

[github_ci.py](tools/github_ci.py) 接受明确的官方仓库、分支及工作流，只选成功且已结束的非 PR 运行。PR 关联列表可能包含下游 fork 的同步 PR，不能单凭这个列表拒绝官方 main push；事件与 head_repository 才是构建身份。

[fetch_managers.py](tools/fetch_managers.py) 的 KernelSU / Next 分支现在：

1. 选择最新可信 CI run，并锁定完整 head SHA、artifact ID、名称和过期状态。
2. 按 artifact ID 下载 ZIP，并核对 GitHub 提供的整个 ZIP digest；该 digest 不是其中 APK 的 digest。
3. 只读取选中的普通 APK 条目，不任意解压整个归档；拒绝空文件、异常大小、符号链接和重复条目。
4. 分别记录 APK SHA-256、CI 身份和源码 UAPI。下载完成不等于签名或运行兼容性已验证。

[verify_manager_apks.py](tools/verify_manager_apks.py) 调用 Android 官方 `apksigner verify` 做密码学验证，然后用 `aapt2` 核对包名、版本和 ARM64 库。它保留生产方签名，不使用 GhostSam 密钥重签管理器。只读取证书指纹、或仅看到绿色 CI，均不能冒充签名验证。

[audit_upstream_ci.py](tools/audit_upstream_ci.py) 从两端共有的明确祖先枚举完整提交集合，独立核对官方 CI 与现有基线。分页不完整或共同祖先不成立时拒绝报“同步完成”。该审计同时记录 manager / kernel UAPI，但不声称移植或设备测试完成。

## 当前发布边界

[Audit upstream CI managers](.github/workflows/audit-upstream-ci.yml) 只抓取、验证并输出候选管理器与来源报告；不构建或加载内核，不创建 Release。其 artifact 名为 `upstream-ci-manager-candidates`。

现有 [auto-build.yml](.github/workflows/auto-build.yml) 显式只获取已固定的 SukiSU-Nova 管理器，避免新增抓取规格后意外把 UAPI 6 管理器配给旧三星内核。Nova fork、版本和载荷不改，BakaSU 也不恢复。

把新管理器放进正式 Release 前，仍需对应三星生产产物的不可变源码、真实内核版本/UAPI、构建身份与 SHA-256，以及独立兼容性验证。本次没有移植内核代码，不能把“最新版候选 APK 已下载”称为“最新版三星适配已完成”。

## 可运行的只读检查

```bash
python3 -m unittest discover -s tools/tests -v
python3 tools/fetch_managers.py --only default --only next --out ci-managers
python3 tools/verify_manager_apks.py --only default --only next \
  --managers ci-managers \
  --apksigner "$ANDROID_HOME/build-tools/36.0.0/apksigner" \
  --aapt2 "$ANDROID_HOME/build-tools/36.0.0/aapt2"
python3 tools/audit_upstream_ci.py \
  --managers ci-managers/managers.json --out ci-managers/source-audit.json
```

上述步骤只读 GitHub 与工作区候选文件，不连接手机，不执行任何候选二进制。
