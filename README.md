<p align="center">
  <strong>简体中文</strong> · <a href="README_EN.md">English</a>
</p>

<h1 align="center">DeepLaw</h1>

<p align="center">
  <strong>本地单用户 Agent Knowledge OS</strong><br />
  Immutable evidence · Markdown-native knowledge · Trusted Ledger · Bounded context
</p>

<p align="center">
  <a href="https://github.com/Eysn0130/DeepLaw/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Eysn0130/DeepLaw/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="https://github.com/Eysn0130/DeepLaw/releases/tag/v0.9.0"><img src="https://img.shields.io/badge/latest-v0.9.0-17202A?style=flat-square" alt="Latest release v0.9.0" /></a>
  <img src="https://img.shields.io/badge/autonomous%20knowledge-0.8%20%2B%200.9-36CDBB?style=flat-square" alt="Autonomous Knowledge Core and Living Wiki" />
  <img src="https://img.shields.io/badge/Python-3.11%E2%80%933.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11 through 3.13" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-2D3748?style=flat-square" alt="Apache 2.0" /></a>
</p>

DeepLaw 不替代 Codex、Claude Code、OpenCode 或其他 Agent Runtime。它在本机为这些宿主提供长期
记忆、知识关系、Living Wiki、来源追踪、检索编排与任务级 Knowledge Capsule，同时保持模型、会话
和通用工具仍由宿主控制。

`v0.9.0` 已交付 0.8 Autonomous Knowledge Core 与 0.9 Living Wiki / Knowledge Intelligence。
旧版 proposal/review 工作流只保留为来源编译、外部导入和迁移兼容面，不再是 Agent 派生知识的
默认激活路径。当前契约与迁移边界见
[`docs/AUTONOMOUS_KNOWLEDGE_OS.md`](docs/AUTONOMOUS_KNOWLEDGE_OS.md)。

## 核心边界

- **local-first、single-user、owner-controlled**：规范状态默认只在本机；无远程 canonical
  database、内容遥测或隐式联网。
- **双知识平面**：官方/用户原件进入不可变证据平面；任务结论、经验、概念、关系、记忆、Wiki 和
  Skill 进入 Agent 自主知识平面。Ledger 与索引是支撑层，不是第三种 Authority。
- **Markdown-native，不是 Markdown-only**：Agent 派生知识的正文和开放元数据以稳定 ID 的
  Markdown revision 承载；SQLite Ledger 裁决 authority、scope、sensitivity、双时态、writer、
  lineage 与审计；原始字节进入内容寻址对象仓库。
- **自主写入不等于权威升级**：已授权 Agent 知识可以直接 `active`，但始终
  `origin=agent_derived`、`authority=agent_derived`、`legal_authority=false`。来源伪造、权限扩大、
  存储型 prompt injection 和治理字段篡改进入 quarantine。
- **查询与写入分离**：`knowledge_support` 永久只读；`knowledge_sink` 是另一个进程、另一个 leaf，
  必须由 owner 显式创建 scope-bound grant 后才能启动。`law_support` 继续独立且只读。
- **法律资料不混权威**：官方 Legal Pack、用户私有法律库和 Agent 派生解释分别准入与展示；排名、
  embedding、图权重和模型 confidence 都不能产生官方身份或法律适用结论。

```mermaid
flowchart LR
  E["官方或用户原始资料"] --> CAS["不可变对象仓库\nbytes · hash · fragment · locator"]
  A["任务、用户陈述、工具结果"] --> G["策略门禁\nscope · sensitivity · provenance · injection"]
  G --> M["Markdown Knowledge Revision"]
  CAS --> L["SQLite trusted identity/event Ledger"]
  M --> L
  L --> D["可重建派生层\nFTS · dense · graph · community · wiki · canvas"]
  D --> Q["Discovery → Admission → Selection"]
  L --> Q
  Q --> C["有界 Knowledge Capsule"]
  C --> R["只读 knowledge_support"]
  S["显式 knowledge_sink grant"] --> G
```

## 快速开始

正式版本使用 Python 3.11+ 与 [`uv`](https://docs.astral.sh/uv/)：

```bash
uv tool install \
  https://github.com/Eysn0130/DeepLaw/releases/download/v0.9.0/deeplaw-0.9.0-py3-none-any.whl
deeplaw --version
```

开发工作树：

```bash
uv sync --all-extras

# 新 Vault 默认同时安装 Markdown-native autonomous core；不会自动启用写权限
uv run deeplaw knowledge init --vault ./vault --name my-project --scope project

# owner 显式创建最小权限 grant；token 只写入 Vault 内 owner-only capability 文件
uv run deeplaw knowledge sink enable \
  --vault ./vault \
  --writer-id codex-local \
  --scope project \
  --max-sensitivity private
```

普通 Agent grant 默认只能记录 `agent_self_report`。如需记录 `user` 或 `external_check` 评价，
owner 必须用 `--feedback-evaluator-type` 创建独立、最小权限的评价 grant；评价标签不能由写入 Agent
自行提升。未传 `--operation` 时 grant 只允许 `remember`；其他 mutation 必须逐项显式加入。
`remember` 只承载普通 Knowledge Object，不能代替 `upsert_concept`、`save_synthesis` 或
`save_skill`；这些语义更强的类型必须由 grant 和请求同时选择对应 operation。

把一次 mutation 写成 closed JSON contract；每次调用都要求 idempotency key 与案件数据边界确认。
凡是声明 `run_id` 的知识必须先提交同 scope/sensitivity 的不可变 Run Record，不能用一个自由文本 ID
伪造 provenance：

```json
{
  "operation": "record_run",
  "idempotency_key": "run-42-record",
  "confirm_no_case_data": true,
  "run_id": "run-42",
  "task": "Prepare the release decision.",
  "host_id": "codex-local",
  "model_id": "host-model",
  "status": "succeeded",
  "scope": "project",
  "sensitivity": "private"
}
```

随后提交 Knowledge Revision：

```json
{
  "operation": "remember",
  "idempotency_key": "run-42-release-decision",
  "confirm_no_case_data": true,
  "title": "Release uses one commit coordinator",
  "body": "All durable knowledge writes pass through the shared coordinator.",
  "kind": "decision",
  "scope": "project",
  "sensitivity": "private",
  "run_id": "run-42",
  "model_id": "host-model",
  "tool_id": "codex"
}
```

```bash
uv run deeplaw knowledge sink apply \
  --vault ./vault \
  --grant-id grant_REPLACE_WITH_RETURNED_ID \
  --request ./remember.json

uv run deeplaw knowledge autonomy recall \
  --vault ./vault --query "release coordinator"

uv run deeplaw knowledge autonomy explain \
  --vault ./vault --query "release coordinator"

uv run deeplaw knowledge autonomy graph --vault ./vault --limit 20

uv run deeplaw knowledge autonomy identity \
  --vault ./vault --query "release coordinator"

uv run deeplaw knowledge autonomy gaps --vault ./vault

uv run deeplaw knowledge autonomy context \
  --vault ./vault \
  --task "prepare the release" \
  --confirm-no-case-data

uv run deeplaw knowledge autonomy verify --vault ./vault
uv run deeplaw knowledge sink status \
  --vault ./vault --grant-id grant_REPLACE_WITH_RETURNED_ID
```

已有 `v0.7` Vault 使用带预迁移备份的显式路径：

```bash
uv run deeplaw knowledge autonomy migrate --vault ./vault --backup ./vault-v07-backup
uv run deeplaw knowledge autonomy rollback \
  --vault ./vault --backup ./vault-v07-backup --confirm
```

## 开放 Markdown 工作面

Vault 使用稳定 ID，而不是路径作为身份。Obsidian、Tolaria 或普通编辑器可以 rename/move 文件；内容
变化只有经过 reconcile 才成为带 writer、hash、parent revision 与 Ledger event 的新版本。重复 ID、
陈旧 base revision、来源/authority/scope 篡改都会保留冲突副本并恢复当前可信版本。
当工作面存在尚未 reconcile 的正文变化时，CLI 或 Sink 的直接更新会拒绝覆盖并先保存冲突；路径
仍在受限工作区内的 rename/move 会保持稳定 ID，后续直接 revision 不会把它移回默认文件名。

```bash
uv run deeplaw knowledge autonomy reconcile \
  --vault ./vault \
  --grant-id grant_REPLACE_WITH_RETURNED_ID \
  --confirm-no-case-data

# 显式前台 Watcher；每轮仍调用同一 reconcile/commit coordinator
uv run deeplaw knowledge autonomy watch \
  --vault ./vault \
  --grant-id grant_REPLACE_WITH_RETURNED_ID \
  --confirm-no-case-data --interval 2

uv run deeplaw knowledge autonomy lint --vault ./vault
uv run deeplaw knowledge autonomy rebuild --vault ./vault

# 遗忘后先 dry-run，再由 owner 明确确认擦除 eligible Agent Knowledge bytes
uv run deeplaw knowledge autonomy gc --vault ./vault
uv run deeplaw knowledge autonomy gc --vault ./vault --no-dry-run --confirm \
  --reason "owner retention policy"

# 只把含显式 completion criterion 的 Procedure 编译为受治理 draft Skill
uv run deeplaw knowledge autonomy skill-draft \
  --vault ./vault --grant-id grant_REPLACE_WITH_RETURNED_ID --request ./skill-draft.json
```

典型布局：

```text
vault/
├── sources/                 # 原件视图；实际字节同时进入 CAS
├── knowledge/               # claim/concept/entity/event/decision/procedure/...
├── memory/                  # working/episodic/semantic/procedural/reflective
├── skills/                  # versioned Skill Knowledge Objects
├── wiki/                    # 可重建 Living Wiki 导航
├── canvas/                  # 可重建 JSON Canvas
├── .deeplaw/
│   ├── objects/sha256/      # immutable content-addressed objects
│   ├── ledger.sqlite3       # identity, governance, bitemporal state, audit
│   ├── capabilities/        # owner-only sink tokens
│   ├── staging/             # crash recovery and preserved conflicts
│   └── derived/             # rebuildable indexes and manifests
└── vault.sqlite3            # only an unmigrated v0.7 compatibility location
```

## Agent 接入

| 进程 / leaf | 权限 | 用途 |
| --- | --- | --- |
| `deeplaw knowledge mcp --stdio` / `knowledge_support` | 只读 | v3 federated source-derived/autonomous recall、exact get、lineage、graph、identity、gaps、Wiki、verify、Capsule |
| `deeplaw knowledge sink mcp --grant-id … --stdio` / `knowledge_sink` | 显式、scope-bound mutation | Run/capture、Claim/Concept/Entity/Event/Comparison/Synthesis/Memory、relation、feedback、consolidate、expire/forget、Skill revision |
| `deeplaw mcp --stdio` / `law_support` | 只读、独立存储 | 官方与用户私有法律证据，以及显式分区的 authority-aware federated context；单分区最多五张 evidence cards |

默认 `deeplaw-knowledge-os` 插件只注册 `knowledge_support`。启用 `knowledge_sink` 必须由 owner 在宿主
配置中单独添加进程和具体 grant ID；插件、Skill、检索内容和模型都不能自行创建 grant 或扩大权限。

## 已实现、兼容与未宣称

| 状态 | 内容 |
| --- | --- |
| **Current** | v0.9.0：CAS 原件/Markdown revision、`.deeplaw/ledger.sqlite3` STRICT Ledger、Run/capture、hash-chained events、稳定 ID/alias/identity resolution、原子 staging/recovery、file lease/CAS/reconcile/Watcher/conflict、active/quarantine/consolidate/expire/forget/owner GC、evidence-bound typed temporal relation、FTS + offline Dense/Reranker + graph + historical lexical recall、Living Wiki/Semantic Lint/Gap/community/Canvas、Skill draft Factory、authority-partitioned Legal context、独立读写 MCP、snapshot/restore 与 v0.7 rollback |
| **Compatibility** | v0.7 Source IR、reviewed Knowledge Asset、proposal Inbox、Workbench、retrieval fabric 和 package 命令仍可使用；`knowledge_support` 在迁移后以独立分区联合旧 source-derived 结果 |
| **External closure pending** | 默认本地 Dense/Reranker 已落地；真实三宿主模型任务、实际具名竞争基线、秘密 held-out、置信区间与两家独立机构签名仍只能由外部事实完成，机器 claim gate 在此之前保持关闭 |
| **Not claimed** | 没有远程 SaaS、多人控制平面、自动法律适用/裁判、模型自授予权限，也没有在缺少冻结候选、held-out 数据、置信区间和独立复现时宣称领先或 SOTA |

## 安全与验证

- 导入文件、网页、Markdown、Wiki、tool result、模型输出和 memory 都是不可信数据。
- 新来源的 CLI 事务成功后会同步 exact bytes/CAS evidence binding；`knowledge doctor` 与
  `autonomy verify` 同时检查 legacy 和 autonomous 规范面，未同步的旁路写入会 fail closed。
- `restricted`、越权 scope、本地绝对路径、capability token、凭据和案件资料不得经 Agent 查询面输出。
- 官方 catalog 在解析和下载前对 exact bytes 做 Ed25519 验签，并实施 key revocation、catalog identity、
  sequence 与回滚防护；网络 catalog 不接受 unsigned-development bypass。
- 自主 snapshot 包含规范 Markdown、对象仓库、Ledger、Inbox provenance artifacts 与 capability
  状态（包括 owner-only capability material）；默认还保留 resumable operation/source-snapshot 与
  retrieval-profile operator state，`--no-include-operator-state` 可显式排除后者。它不包含可重建的
  Wiki、Canvas 和检索 cache；snapshot 必须按凭据等级保护、不得提交或分享，恢复后必须重建派生层。
- 任何 benchmark 结论必须固定 corpus、split、模型、prompt、权限、预算、硬件、网络和成本口径。

交付前验证：

```bash
uv run pytest
uv run ruff check .
git diff --check
```

## 文档

| 文档 | 说明 |
| --- | --- |
| [`docs/AUTONOMOUS_KNOWLEDGE_OS.md`](docs/AUTONOMOUS_KNOWLEDGE_OS.md) | 当前自主内核契约、迁移、CLI/MCP、安全与限制 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | v0.9 当前系统架构、事务边界与派生层 |
| [`docs/KNOWLEDGE_OS.md`](docs/KNOWLEDGE_OS.md) | v0.7 兼容面和当前覆盖说明 |
| [`docs/DEEPLAW_2.md`](docs/DEEPLAW_2.md) | 双层 Legal Pack 与法律检索边界 |
| [`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md) | Codex、Claude Code、OpenCode 的薄适配与隔离 |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | 可复现评测与领先性证据边界 |
| [`docs/V0_9_ACCEPTANCE_MATRIX.md`](docs/V0_9_ACCEPTANCE_MATRIX.md) | v0.9 验收矩阵、发布门禁与外部证据缺口 |
| [`docs/RELEASE_NOTES_v0.9.0.md`](docs/RELEASE_NOTES_v0.9.0.md) | v0.9.0 发布说明与升级边界 |
| [`SECURITY.md`](SECURITY.md) | 威胁模型和安全报告渠道 |

DeepLaw 以 [Apache License 2.0](LICENSE) 开源。请勿提交法律原件、生成 release database、凭据、
签名私钥、私有笔记或包含用户材料的本地路径。
