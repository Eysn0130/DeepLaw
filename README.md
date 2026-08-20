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
  `kernel_release_core`，Gate classification：v9。
- `release_ready=false`，尚无 `0.13.0` tag 或 release。
- 当前 Provider advertisement：knowledge-support input v7 / output v6，仅 `query`、`context`、
  `explain`；input v1-v6 和 output v1-v5 仅为 compatibility/internal。
- 本地 regression、mock、dry-run、旧报告或 no-model smoke 不构成真实 Host、Human Gold、法律专家、
  3 OS、scale、供应链或发布证据。Kernel Release Core、Capability 与 Competitive/Research
  Claim 分别判定；缺失的可选能力或研究证据保持 `not_executed`，只禁止对应声明。
- v0.13 Kernel 的支持上限为每个 Vault 10,000 个 active governed Knowledge Objects；>10k
  为实验范围，100k 与其 sharding/bundling 不属于 v0.13。官方 signed Legal Pack、GUI/Desktop
  interoperability、semantic restore 与 Claude 未经各自证据不得宣称发布。

机器状态只读取
[`benchmarks/v013/active-qualification-v3.json`](benchmarks/v013/active-qualification-v3.json) 和
[`benchmarks/release/v013-gate-classification-v9.json`](benchmarks/release/v013-gate-classification-v9.json)；
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

## 首要产品旅程

先建立并检查本地 Vault。`doctor` 必须报告 canonical/autonomous readiness；缺失前置条件时应返回
可操作 Gap，而不是继续连接 Host。

```bash
deeplaw knowledge init --vault ./vault --name my-project --scope project
deeplaw knowledge doctor --vault ./vault
```

仓库开发环境提供一个可复制、公开、source-free、无模型的最短成功流程。它在新目录中执行
Source add、owner source review、只读 Host handoff、现有 Coordinator/MCP grant 编译、Query、
Context，以及 Wiki 到 exact Source Revision 的下钻，并在 JSON 中逐项报告结果：

```bash
uv run python -m examples.living_wiki.run_demo \
  --workspace /tmp/deeplaw-living-wiki-demo
```

该流程是本地 development evidence，不是真实 Host、外部 benchmark、qualification 或 release
evidence。完整的逐步 CLI/Host packet 工作流见
[`docs/LIVING_WIKI_COMPILER.md`](docs/LIVING_WIKI_COMPILER.md#cli-workflow)。

正式 MCP 配置必须使用闭合环境入口；以下命令用于 owner 诊断，静态 Host 配置由后续
`host connect` 生成同一 argv：

```bash
deeplaw knowledge mcp --closed-environment --stdio
deeplaw mcp --closed-environment --stdio
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
deeplaw knowledge sink enable --vault ./vault \
  --writer-id owner-host-continuity --scope project --max-sensitivity private \
  --operation record_run --operation remember --operation forget
deeplaw knowledge task enroll-host-session --vault ./vault \
  --host codex \
  --task-handle TASK_HANDLE --workspace . --grant-id GRANT_ID \
  --idempotency-key BIND_IDEMPOTENCY_KEY --confirm-no-case-data \
  < OWNER_ONLY_OFFICIAL_SESSION_ID
deeplaw knowledge task checkpoint --vault ./vault \
  --task-handle TASK_HANDLE --workspace . --grant-id GRANT_ID \
  --idempotency-key CHECKPOINT_IDEMPOTENCY_KEY \
  --summary 'Bounded verified progress.' --next-action 'Continue the selected task.' \
  --expires-at '2099-01-01T00:00:00Z' --confirm-no-case-data
deeplaw knowledge task resolve-host-continuity --vault ./vault \
  --host codex --session-sha256 SESSION_SHA256_FROM_ENROLLMENT_RESULT --workspace .
deeplaw knowledge task resume --vault ./vault \
  --project DeepLaw --task 'Finish the selected task.' --workspace .
deeplaw knowledge task timeline --vault ./vault \
  --task-handle TASK_HANDLE --workspace .
```

普通恢复不要求 task handle。fork、compaction、stale checkpoint、wrong task/worktree、ambiguous
binding 和 selective forget 都必须重新校验当前状态并 fail closed 为结构化 Gap。

只导入来源尚未产生可 Admission 的编译知识。此时 `context` 必须返回
`uncompiled_source` Gap；不能把它描述为成功旅程。完成上述 source review、handoff、grant 和现有
Coordinator 编译后，再通过同一 Context Compiler 获得有界上下文；精确引用任务按需下钻到
Source Revision、Fragment 和 Locator，而不是把整个来源复制进 Wiki 或 Provider。

```bash
deeplaw knowledge source add --vault ./vault --source ./guide.md \
  --confirm-no-case-data
deeplaw knowledge compile handoff --vault ./vault \
  --source-revision-id sourcerev_REPLACE
```

## 固定边界

- 自主写入不等于权威升级。embedding、图权重、模型 confidence、引用次数或使用频率都不能产生
  官方身份、法律 Authority 或法律适用结论。
- `knowledge_support` 永久只读；`knowledge_sink` 是独立、显式 grant 控制的写进程；
  `law_support` 独立只读。
- 不自动读取或保存 prompt、transcript、hidden reasoning、auth、Secret 或 raw log。
- Provider 不得收到路径、session hash、内部 selection/receipt identity、未 admitted 内容或 Secret。
- 普通 read 不写 canonical Ledger；只有 durable mutation 进入共享 Coordinator。
- 不增加远程 canonical storage、telemetry、GUI/云控制平面或新知识引擎。

完整规范与子系统导航见 [`docs/README.md`](docs/README.md)，安全边界见
[`SECURITY.md`](SECURITY.md)。DeepLaw 采用 [Apache License 2.0](LICENSE)。
