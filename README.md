<p align="center">
  <strong>简体中文</strong> · <a href="README_EN.md">English</a>
</p>

<h1 align="center">DeepLaw</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="820" alt="DeepLaw 2.0 产品品牌" />
</p>

<p align="center">
  <strong>Local-first Agent Knowledge OS</strong><br />
  <sub>Source-native Evidence · Governed Living Wiki · Verifiable Context</sub>
</p>

DeepLaw 将原始资料编译为受治理的知识与 Living Wiki，并为当前任务返回有界、可验证的
Knowledge Capsule。它不是普通 RAG、完整 transcript 仓库、Obsidian 替代品、法律裁判系统或
Agent runtime。

架构冻结为一个共享治理内核上的三个产品角色：

- Task Continuity / Governed Project Knowledge；
- Source-native Evidence Library；
- Living Wiki。

三者共用一个 Context Compiler：
`Discovery → Admission → Selection → Bounded Verifiable Knowledge Capsule`。Context Compiler
不是第四产品或第二检索引擎；Legal Pack 是 Evidence Library 的第一方法律策略面。专业来源保留
原始字节、版本、Fragment 和 Locator，Wiki 是可重建投影，不是完整可编辑 canonical 副本。

## 当前诚实状态

- 公开 package/main：`0.12.0 Beta`；最新 tag：`v0.12.0`。
- Active qualification：`machine_evaluation_pending`，profile：
  `machine_evaluated_no_human_attestation`，Gate classification：v8。
- `release_ready=false`，尚无 `0.13.0` tag 或 release。
- 当前 Provider advertisement：knowledge-support input v7 / output v6，仅 `query`、`context`、
  `explain`；input v1-v6 和 output v1-v5 仅为 compatibility/internal。
- 本地 regression、mock、dry-run、旧报告或 no-model smoke 不构成真实 Host、Human Gold、法律专家、
  3 OS、scale、供应链或发布证据。缺失资格保持 `not_executed`。

机器状态只读取
[`benchmarks/v013/active-qualification-v2.json`](benchmarks/v013/active-qualification-v2.json) 和
[`benchmarks/release/v013-gate-classification-v8.json`](benchmarks/release/v013-gate-classification-v8.json)；
README 不承担第二状态台账。

## 安装

正式版本使用 Python 3.11+ 与 [`uv`](https://docs.astral.sh/uv/)：

```bash
uv tool install \
  https://github.com/Eysn0130/DeepLaw/releases/download/v0.12.0/deeplaw-0.12.0-py3-none-any.whl
deeplaw --version
```

仓库开发环境：

```bash
uv sync --all-extras
```

## 真实首要旅程

先建立并检查本地 Vault。`doctor` 必须报告 canonical/autonomous readiness；缺失前置条件时应返回
可操作 Gap，而不是继续连接 Host。

```bash
deeplaw knowledge init --vault ./vault --name my-project --scope project
deeplaw knowledge doctor --vault ./vault
```

建立任务线并生成 task-neutral、只读、人工合并的 Host 配置。静态 `host connect` 不选择 task，
不管理 Host 登录或 runtime，也不启用 `knowledge_sink`。

```bash
deeplaw knowledge task start --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge task locate --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge host connect --host codex --vault ./vault
```

首次 session 绑定是显式 owner mutation，要求现有 Sink grant、幂等键和当前 workspace。首选入口只从
stdin 读取一次 raw official session ID，并立即绑定它的 SHA-256；raw ID 不得出现在 argv、Ledger、
日志、receipt 或 Provider。`bind-host-session` 仅保留给已经由 owner 安全计算 SHA-256 的调用方。

```bash
deeplaw knowledge task enroll-host-session --vault ./vault \
  --host codex \
  --task-handle TASK_HANDLE --workspace . --grant-id GRANT_ID \
  --idempotency-key BIND_IDEMPOTENCY_KEY --confirm-no-case-data \
  < OWNER_ONLY_OFFICIAL_SESSION_ID
deeplaw knowledge task resolve-host-continuity --vault ./vault \
  --host codex --session-sha256 SESSION_SHA256_FROM_ENROLLMENT_RESULT --workspace .
deeplaw knowledge task resume --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
```

普通恢复不要求 task handle。fork、compaction、stale checkpoint、wrong task/worktree、ambiguous
binding 和 selective forget 都必须重新校验当前状态并 fail closed 为结构化 Gap。

导入来源后，通过同一 Context Compiler 获得有界上下文；精确引用任务按需下钻到 Source Revision、
Fragment 和 Locator，而不是把整个来源复制进 Wiki 或 Provider。

```bash
deeplaw knowledge source add --vault ./vault --source ./guide.md \
  --confirm-no-case-data
deeplaw knowledge context --vault ./vault --task 'Verify the guide.' \
  --purpose verify --confirm-no-case-data
```

## 固定边界

- `knowledge_support` 永久只读；`knowledge_sink` 是独立、显式 grant 控制的写进程；
  `law_support` 独立只读。
- 不自动读取或保存 prompt、transcript、hidden reasoning、auth、Secret 或 raw log。
- Provider 不得收到路径、session hash、内部 selection/receipt identity、未 admitted 内容或 Secret。
- 普通 read 不写 canonical Ledger；只有 durable mutation 进入共享 Coordinator。
- 不增加远程 canonical storage、telemetry、GUI/云控制平面或新知识引擎。

完整规范与子系统导航见 [`docs/README.md`](docs/README.md)，安全边界见
[`SECURITY.md`](SECURITY.md)。DeepLaw 采用 [Apache License 2.0](LICENSE)。
