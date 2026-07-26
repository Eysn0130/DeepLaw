<p align="center">
  <strong>简体中文</strong> · <a href="README_EN.md">English</a>
</p>

<h1 align="center">DeepLaw - 2.0</h1>

<p align="center">
  <img src="assets/brand/deeplaw-2-glass.png" width="820" alt="DeepLaw 2.0 品牌字标" />
</p>

<p align="center">
  <strong>面向下一代 AI Agent 的本地优先知识操作系统。</strong><br />
  将信息、项目经验、工具结果与领域资料编译为可验证、可演化的 Knowledge Assets。
</p>

<p align="center">
  <a href="https://github.com/Eysn0130/DeepLaw/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Eysn0130/DeepLaw/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <img src="https://img.shields.io/badge/version-v0.4.0-17202A?style=flat-square" alt="Version v0.4.0" />
  <img src="https://img.shields.io/badge/Python-3.11%E2%80%933.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11 through 3.13" />
  <img src="https://img.shields.io/badge/MCP-read--only-18A999?style=flat-square" alt="Read-only MCP" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-2D3748?style=flat-square" alt="Apache 2.0" /></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#deeplaw-架构">系统架构</a> ·
  <a href="#evidence-compiler">Evidence Compiler</a> ·
  <a href="#agent-接入">Agent 接入</a> ·
  <a href="#当前收录与更新">当前收录</a> ·
  <a href="#文档">文档</a>
</p>

---

<p align="center">
  <img src="assets/readme/product-flow-glass.png" width="1180" alt="文件进入 DeepLaw 2.0 知识库，经定位、连接和解释形成证据包并交付给 Agent" />
</p>

DeepLaw 2.0 为已有 Agent 提供独立知识层，不替代 Codex、Claude Code、OpenCode 的推理与执行。
它把文件、项目决策、约束、经验和工具结果保存为有来源、有生命周期、有敏感级别的
**Knowledge Assets**，再按具体任务编译为小型、可核验的 **Knowledge Capsule**。

中国法律能力作为首个严格领域包继续独立运行：法源进入不可变、版本化的 Legal Pack，Agent
只接收当前问题需要的 **Evidence Pack**。通用知识与法律知识不混库、不共享更新权限，也都不
接管 Analytix 案件项目。

## 核心能力

| 能力 | DeepLaw 2.0 的做法 |
| --- | --- |
| **Knowledge Compiler** | 保留原始文件和定位片段，再生成待复核资产；摘要或语义单元不能替代来源 |
| **生命周期** | `proposed → active → superseded/revoked`，风险内容进入 `quarantined`；只有人工复核可激活 |
| **Context Compiler** | 依据任务优先选择约束、决策、规则和经验，以正文、来源元数据和完整载荷三重硬预算生成 Capsule |
| **长期知识** | 区分 working、project、experience、wisdom、domain；临时知识强制过期，不保存整段会话 |
| **隔离与敏感级别** | 每个 vault 使用独立 owner-only SQLite；public/internal/private/restricted 决定导出与 Agent 可见性 |
| **可验证与可迁移** | 事件链与当前 Asset/source/relation/FTS 状态双重核对，所选原件重新验 hash；`.dlk` 导入永远先进入不可信隔离区 |
| **人类可维护** | SQLite 是规范存储，Markdown/Obsidian 是可重建视图，关系和反向链接用于审阅 |
| **严格领域包** | Legal Pack 继续提供版本、时效、来源、Evidence Pack 与 receipt，不被通用知识层降级 |
| **宿主低干扰** | 两个独立、显式使用、只读的单 leaf MCP 插件；普通代码和数据任务不自动激活 |

## DeepLaw 架构

DeepLaw 使用一个通用内核和一个独立 Legal Pack：

```text
Project Sources / Decisions / Experience / Tool Results
  → Knowledge Compiler
  → owner-only Knowledge Asset Vault
  → human review + lifecycle
  → Context Compiler
  → bounded Knowledge Capsule
  → knowledge_support → Agent

Reviewed Legal Sources
  → Document IR
  → immutable Legal Pack Release
  → Evidence Compiler
  → bounded Evidence Pack
  → law_support → Agent
```

- **Knowledge Asset Vault**：保存来源片段、资产状态、显式关系和审计链；持久写入只在本地
  CLI 发生，Agent 只读；
- **Knowledge Capsule**：保存任务、预算、所选资产、来源、缺口、摘要和 vault 审计锚点；
- **Legal Pack Release**：仍以 `mode=ro&immutable=1` 打开，保存法源版本、block、segment、
  时效、关系、风险与 hash；
- **Markdown 派生视图**：供人工浏览、校对和 Obsidian 使用，可删除重建，不是运行时真源；
- **插件隔离**：`knowledge_support` 与 `law_support` 属于不同插件、不同进程和不同激活意图。

### Knowledge Asset 闭环

```text
Ingest → Propose/Quarantine → Human Review → Active
  → Search/Context → Verify → Feedback Proposal
  → Human Review → Supersede/Revoke
```

DeepLaw 不向 Agent 暴露 `remember` 或 `learn` 写工具。调试经验和 Capsule 反馈只能形成 proposal，
且反馈必须绑定一份验证通过的 Capsule 文件，不能伪造 Capsule ID。带有指令式文本或不可见
控制字符的来源会失败关闭到 quarantine；激活这类资产除 `--confirm-reviewed` 外还必须显式提供
`--confirm-quarantine`。即使是已复核资产，也只有 constraint/rule/procedure 可以标记为
`reviewed_instruction`，且不能覆盖宿主、仓库或用户指令。

通用 Knowledge Asset 一律输出 `legal_authority: false`。它可以保存项目规则或用户提供的领域
参考，但不能冒充官方法源；需要中国法律权威来源时必须切换到独立的 `law_support`。

### Legal Pack：从文件到证据的知识闭环

<p align="center">
  <img src="assets/readme/knowledge-cycle.png" width="1120" alt="DeepLaw 2.0 的 Ingest、Organize、Locate、Connect、Explain 与 Verify 知识闭环" />
</p>

| 动作 | 作用 | 约束 |
| --- | --- | --- |
| **Ingest** | 校验文件、解析内容并建立 Document IR | 处理成功不等于人工审核通过 |
| **Organize** | 保存层级、顺序、版本与 Knowledge Map | 派生摘要不能覆盖原文 |
| **Locate** | 定位题名、文号、条款、关键词与相关片段 | 泛词不会展开成无限候选 |
| **Connect** | 连接引用、修订、废止、替代、定义与例外 | 关系连接本身不是法律结论 |
| **Explain** | 生成有来源的导航、短摘要与问题分解 | 派生解释必须回到精确来源片段 |
| **Verify** | 检查来源、时效、证据义务、预算、缺口与回执 | 不为看起来完整而补造答案 |

`Deliver` 是最终动作：只把完成当前任务所需的证据、限制、缺口和回执交给 Agent。

### Evidence Core

<p align="center">
  <img src="assets/readme/evidence-core.png" width="1120" alt="Evidence Core 由来源与版本、知识地图、证据义务、限制与缺口、回执与重放组成" />
</p>

Evidence Core 将五类信息保持在同一条可复核链路中：

- **Sources & Versions**：固定 release、来源 URL、source hash、segment hash 与精确定位；
- **Knowledge Map**：只让有来源的关系进入权威路径，派生关系只能用于发现候选；
- **Evidence Duties**：把问题编译为封闭的证据要求，包括主规则、精确引文、时效、定义、
  解释、程序、数额/立案标准、反证和案例参考；
- **Limits & Gaps**：限制卡片、字符、关系路径和 hop；区分证据缺口、语料缺口、复核缺口、
  时效缺口和抽取缺口；
- **Receipts & Replay**：记录选择结果所绑定的 release、segment 与 hash，使结果可验证、可重放。

## Evidence Compiler

Evidence Compiler 是 DeepLaw 2.0 的核心查询路径。它不直接截取“分数最高的若干片段”，而是
先定义什么才算足以回答当前问题，再选择内容：

```text
Question
  → closed Evidence Duties
  → bounded candidate discovery
  → integrity / relevance / temporal-intent / extraction admission
  → coverage witnesses
  → limitation and counterevidence challenges
  → bounded coverage-first evidence set
  → evidence + uncertain evidence + gaps + receipts
```

在有限候选池和上下文预算内，编译器按确定性优先级先满足精确目标与必需证据义务，再处理
定义、限制、例外、反证和版本变化；只有能新增或改善 witness 的候选才进入结果。这个过程追求
有界覆盖与去冗余，不宣称求解全局最小集合。大量同主题片段不能挤掉精确条文或必要限制；没有
通过能力条件的候选不能产生 coverage witness，也不能把必需证据标记为已覆盖。

单一主题词使用导航模式：只返回经来源锚定的主规则和极短追问，不展开入站引用图。只有明确的
研究问题需要版本、例外、替代或反证关系时，才允许一跳确定性关系进入结果。

Evidence Pack 明确区分：

| 输出 | 含义 |
| --- | --- |
| `evidence` | 通过完整性、相关性及本次查询实际启用的时效/抽取门禁的研究证据；不等于来源身份或现行效力已完成人工审核 |
| `uncertain_evidence` | 与问题相关，但至少一项准入条件尚未满足 |
| `obligation_coverage` | 每项证据义务由哪些可检查 witness 覆盖 |
| `gaps` | 尚未覆盖或无法确认的证据、语料、时效、复核与抽取缺口 |
| `receipt_id` | 可对固定 release 中的片段重新计算 hash 的回执 |

即使问题没有显式询问时效，已知历史、废止、替代或尚未施行的候选在缺少 `as_of` 时也只能进入
`uncertain_evidence`，不能被 Agent 当作主证据。

候选发现可综合题名、条文、相关性和来源层级用于排序，但这个排序分数不能提升完整性、时效、
抽取质量或人工审核状态。模型或派生索引可以帮助发现候选，不能自行判定修订废止、消除阻断性
缺口或把研究候选变成案件适用结论。

## 当前版本 v0.4.0

| 能力 | 当前状态 |
| --- | --- |
| Knowledge Asset | owner-only vault、来源片段、提议/隔离/激活/替代/撤销生命周期、敏感级别、事件链与当前状态重放核对 |
| Context Capsule | 任务优先级、正文/来源/完整载荷预算、显式 gaps、来源引用、一次有界人工关系扩展、选择原因、digest 与历史审计锚点 |
| 经验成长 | Debugger 与 Capsule feedback 只生成待复核 proposal，不允许 Agent 自动写入或自我提升 |
| 共享与人类视图 | 固定 revision 可复现 `.dlk`、不可信导入隔离、确定性 Markdown/Obsidian 投影 |
| 文件处理 | 官方目录支持 DOCX/PDF；用户私有库另支持 UTF-8 TXT；保留 block 级定位与抽取证据 |
| 数据表示 | 不可变原件、Document IR、只读 SQLite release 与可重建 Markdown 派生视图分层 |
| 官方目录 | Ed25519 验签、HTTPS 更新、sequence 防回滚/改写、逐来源大小与 SHA-256 校验 |
| 用户私有库 | owner-only 物理目录、显式增删、独立不可变快照，不与官方结果混排 |
| 定位与连接 | 题名、别名、文号、条款、中文全文检索、来源绑定主题定位与有限关系路径 |
| 证据交付 | 封闭 QueryPlan、启发式证据义务、按查询启用的时效/抽取门禁、有界证据、显式缺口与 receipt |
| Agent 接口 | 两个可独立安装的只读 MCP 插件；每个插件只有一个 leaf tool，没有持久写工具 |
| 宿主 | Codex、Claude Code 与 OpenCode 适配；Analytix 案件项目仍在 DeepLaw 2.0 范围之外 |

## 快速开始

需要 Python 3.11+ 和 [`uv`](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/Eysn0130/DeepLaw.git
cd DeepLaw
uv tool install '.[document-engine]'
deeplaw --version
```

先创建一个独立项目知识 vault：

```bash
deeplaw knowledge init \
  --vault "$HOME/.deeplaw/vaults/my-project" \
  --name my-project \
  --scope project

deeplaw knowledge ingest \
  --vault "$HOME/.deeplaw/vaults/my-project" \
  --source "./ARCHITECTURE.md" \
  --source-kind document \
  --sensitivity internal \
  --confirm-no-case-data
```

摄取只会生成 `proposed` 或 `quarantined` 资产。人工检查输出中的 `asset_id` 后，可以逐项激活；
若已完整复核同一份原件，也可以用输出中的精确 `source_id` 在一个事务内激活该原件的全部
候选：

```bash
deeplaw knowledge approve \
  --vault "$HOME/.deeplaw/vaults/my-project" \
  --asset-id "asset_..." \
  --confirm-reviewed

deeplaw knowledge approve-source \
  --vault "$HOME/.deeplaw/vaults/my-project" \
  --source-id "source_..." \
  --confirm-reviewed

deeplaw knowledge context \
  --vault "$HOME/.deeplaw/vaults/my-project" \
  --task "在不破坏现有存储契约的前提下实施迁移" \
  --confirm-no-case-data \
  --output "./capsule.json"
```

`task` 和 `goal` 会进入 Capsule 文件，因此 context 同样要求
`--confirm-no-case-data`；该确认只允许非案件项目知识，不能把案件事实、聊天或附件带入
DeepLaw。

若资产状态是 `quarantined`，必须在复核风险文本后额外传入
`--confirm-quarantine`；普通 `--confirm-reviewed` 不足以激活。

Capsule、`.dlk` 和 Markdown 导出不会覆盖无关的既有文件或目录；请为新产物选择新路径。

完整生命周期、导出、反馈与安全边界见
[`docs/KNOWLEDGE_OS.md`](docs/KNOWLEDGE_OS.md)。

官方目录包含 PDF。首次安装或更新官方 release 前，还需安装 PDF 渲染、OCR 与简体中文语言数据：

```bash
# macOS (Homebrew)
brew install poppler tesseract tesseract-lang

# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-chi-sim
```

文档模型只通过一次显式管理命令获取；固定 revision 中 15 个文件的大小与 SHA-256 全部通过
后才写入 owner-only 配置。日常摄取不会自动下载模型，也不读取上游环境变量或用户级模型配置：

```bash
deeplaw document-engine setup
deeplaw document-engine status
deeplaw-document-engine --version
pdftoppm -v
tesseract --version
tesseract --list-langs | grep -x 'chi_sim'
```

签名官方目录声明的构建策略是强制策略。`official install` 与 `official update` 会在下载任何官方
原件、构建 release 或切换激活版本前执行同样的严格预检，包括完整重算本地文档模型清单；
缺少或篡改任一文件即终止，不会静默降级，也不能通过 CLI 弱化目录策略。首次 `setup` 会从
固定来源下载约 1.1 GB 模型；已有完整本地缓存时可使用
`deeplaw document-engine setup --local-files-only`。若机器只读取已经构建好的 release，可改用轻量安装
`uv tool install .`。处理用户自己的风险 PDF 时显式选择：

```bash
uv tool install --force --reinstall-package deeplaw '.[document-engine]'
deeplaw document-engine setup
deeplaw private add \
  --source "/path/to/scanned-legal-reference.pdf" \
  --pdf-fallback document-engine \
  --allow-needs-ocr \
  --confirm-no-case-data
```

安装 DeepLaw 2.0 团队维护的官方目录。客户端先验证签名，再从目录记录的官方来源下载原件并在
本机构建不可变 release；仓库不重新分发这些原件。

```bash
deeplaw official install
deeplaw official status
deeplaw doctor
```

需要人工浏览或校对时，可从不可变 release 确定性导出 Markdown；该目录是派生视图，可随时删除
并重建：

```bash
deeplaw export-markdown --output "/path/to/deeplaw-markdown"
```

团队发布新目录后，由用户显式更新：

```bash
deeplaw official update
```

已经持有与目录完全一致的 source package 时，可以复用本地原件：

```bash
deeplaw official install --source-root "/path/to/legal-source-package"
```

官方目录是可选能力，停用或卸载不会触碰用户私有库：

```bash
deeplaw official disable
deeplaw official enable
deeplaw official uninstall
```

用户自己的法律参考资料进入独立的本机私有库。导入需要确认文件不是案件材料；Agent 只能
读取，不能通过 MCP 上传或删除。

```bash
deeplaw private add \
  --source "/path/to/user-legal-reference.docx" \
  --confirm-no-case-data
deeplaw private list
deeplaw private search --query "资料题名 第一条"
deeplaw private delete --document-id "doc_..."
```

## Agent 接入

| 宿主 | 入口 | 激活边界 |
| --- | --- | --- |
| Codex | [`plugins/deeplaw`](plugins/deeplaw) / [`plugins/deeplaw-knowledge-os`](plugins/deeplaw-knowledge-os) | 法律与通用知识为两个独立显式 Skill |
| Claude Code | 同上 | 两个独立插件分别注册单 leaf 只读 MCP |
| OpenCode | [`adapters/opencode`](adapters/opencode) | 默认 deny，由两个专用 agent 分别显式授权 |
| Analytix | [`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md) | 未来按 turn 接入；案件项目库不属于 DeepLaw 2.0 |

Codex 本地安装：

```bash
codex plugin marketplace add /absolute/path/to/DeepLaw
codex plugin add deeplaw@deeplaw
codex plugin add deeplaw-knowledge-os@deeplaw
```

Claude Code 本地安装：

```bash
claude plugin validate --strict /absolute/path/to/DeepLaw/.claude-plugin/marketplace.json
claude plugin marketplace add /absolute/path/to/DeepLaw
claude plugin install deeplaw@deeplaw
claude plugin install deeplaw-knowledge-os@deeplaw
```

每个插件只暴露一个 MCP leaf tool。Legal Pack 的 `law_support` 使用
`search/get/verify/release_info`，用户私有库使用
`private_search/private_get/private_verify/private_info`；Knowledge OS 的
`knowledge_support` 使用 `search/get/context/verify/inspect`。全部 operation 只读。安装插件不会
在后台下载、学习或修改资料，更新、摄取和审核只能由用户显式运行 CLI。

普通任务不应自动激活任一插件。只有用户明确要求项目长期知识或 Knowledge Capsule 时才使用
Knowledge OS；只有明确法律研究时才使用 Legal Pack。宿主应尽可能在工具 schema 进入模型上下文
前完成选择，避免 Token、延迟和任务路由退化。

## 知识边界

| 范围 | 谁可以更新 | 不可变/禁止事项 | Agent 访问 |
| --- | --- | --- | --- |
| 通用 Knowledge Asset vault | 本机 owner 通过 CLI 提议、审核、替代、撤销；原文修改必须生成新 Asset | Agent 不得持久写；source bytes 不原地改；全部资产 `legal_authority=false`；restricted 不出 MCP | 独立 `knowledge_support`；只读 active、非 restricted 资产 |
| 官方团队法律目录 | DeepLaw 团队发布 sequence 递增、Ed25519 验签的新目录；用户只能显式安装、更新、停用或卸载 | Agent 和用户都不能改 release 内法条、版本、时效、来源、segment、关系或审核状态；修正必须新建不可变 release | `law_support` 官方四个只读 operation |
| 用户私有法律资料 | 当前 OS 用户通过 `private add/delete` 增删；每次产生新的独立快照 | 不得进入或覆盖官方目录，不得继承官方权威、排序或审核状态；Agent 不得上传、删除或改写 | 仅显式 `private_*`，始终标记用户提供 |
| Analytix 案件项目 | 仅由 Analytix 的案件项目存储管理 | 案件附件、事实、对话、身份、交易和 SQLite/DuckDB 永不进入 DeepLaw 任一 scope | DeepLaw 不读取、不索引、不共享 |

当前 vault 与本地私有库依赖操作系统账户和 owner-only 文件权限，不是共享服务的多租户认证或
静态加密。案件证据、事实、聊天、身份和交易不能进入任何 DeepLaw scope；Agent 对话也不能整段
自动复制，只有经复核的非案件决策、约束、事实或经验可进入通用 vault。

这里的“Agent 只读”是 DeepLaw 插件/MCP 的接口边界，不是对宿主通用 Shell 的替代沙箱。若宿主
另行授予 Agent 与本机 owner 相同的任意 Shell/文件写权限，它也能触达离线管理 CLI；接入时必须
禁止 Agent 写入 `~/.deeplaw` 和调用管理命令，或让只读 MCP 使用独立 OS 身份运行。

## 文件处理与质量门禁

- **DOCX**：直接解析 OOXML，保留段落、表格行、样式与脚注引用；
- **PDF**：按页保留原生文本、版面块、定位、抽取方法、置信信息与风险标记；低质量页面进入
  多路径解析和视觉复核流程；
- **TXT**：以严格 UTF-8 解析，保存稳定行序与段落定位；
- **Document IR**：为每个 block 建立稳定 ID、顺序、文本 hash、页码/段落、类型、来源和质量状态；
- **Markdown**：只从 IR 生成用于浏览与校对的派生视图，不作为切分、检索或法律引用的真源。

质量判断落实在页和 segment，而不是粗暴地污染整份文档。未通过抽取门禁的片段只进入
`uncertain_evidence`，不会成为已核验主证据；详细逐页状态、方法、hash 与审计记录保存在
release 构建报告中，修正通过新的不可变 release 发布。

## 质量验证

```bash
uv lock --check
uv run ruff check .
uv run pytest
uv run deeplaw eval --cases evals/core-v0.4.0-2026-07-25.jsonl --limit 5
git diff --check
```

当前可复现 smoke 集覆盖精确定位、时效分桶、抽取门禁、官方/私有隔离和 receipt 往返校验。
Knowledge OS 测试另覆盖 vault 权限与隔离、生命周期与显式替代、数据库/FTS/源文件篡改、
来源指令隔离和双重风险确认、Context 预算与有界关系扩展、Capsule ID 伪造/篡改/过期/撤销、
`.dlk` 结构与 trust laundering、Markdown 安全替换、完整 CLI 生命周期和真实 stdio MCP。
测试所绑定的 release、database、case、源码、环境、hash 与指标见
[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)。跨系统性能结论需要在相同语料、问题、模型和上下文
预算下使用外部 held-out 数据验证。

外部证明协议、逐题评分器、独立签名证据清单和机器宣称门禁已经冻结；当前证据状态仍是
`pending_external_execution`，因此不会生成跨系统领先文案。协议与当前限制见
[`docs/EXTERNAL_BENCHMARK_PROTOCOL.md`](docs/EXTERNAL_BENCHMARK_PROTOCOL.md)。

通用 Knowledge OS 另有一份源码绑定、但不参与领先宣称的 10 万资产诊断：
100 个长任务查询的 search Hit@1、Capsule 召回和 Capsule 校验均为 `1.0`；常驻只读进程
search p95 为 `0.82 ms`、context p95 为 `1.28 ms`。冷 CLI 会先完整重放审计与当前状态，
本次约 `5.85 s`、峰值约 `443 MB`，因此宿主运行时应使用常驻 MCP。完整环境、实现 hash 和
限制见 [`benchmarks/scale/knowledge-scale-100k-2026-07-26.json`](benchmarks/scale/knowledge-scale-100k-2026-07-26.json)；
该合成诊断固定为 `claim_eligible=false`，也不外推到百万资产。

## 安全与责任边界

- DeepLaw 2.0 返回可复核的研究证据，不替代法律意见、事实认定或裁判结论；
- Knowledge Asset 的 `trust` 是来源标签，不是真实性评分；模型输出和反馈不能自动激活；
- 普通用户不能自行声明 `verified_source`；通用资产永远不是法律权威，法源研究必须走 Legal Pack；
- `.dlk` v1 校验内容完整性但尚未签名发布者身份，任何导入都先进入 untrusted quarantine；
- 查询时不会把公网内容直接加入主证据，模型也不能自行决定修订、废止、冲突或优先级；
- 用户私有资料不能改变官方 release、审核状态、排序或更新生命周期；
- 受限法源与案件信息不得进入 issue、PR、日志、截图或公开 benchmark。

语料治理见 [`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md)，安全报告流程见
[`SECURITY.md`](SECURITY.md)。

## 路线图

- [x] 不可变 Knowledge Release、Document IR、receipt 与只读 MCP
- [x] 官方签名目录生命周期与用户私有法律资料物理隔离
- [x] 精确定位、证据义务、时效/抽取门禁和显式 gaps
- [x] Codex、Claude Code 与 OpenCode 适配
- [x] Knowledge Asset vault、人工复核生命周期、Context Capsule 与只读 MCP
- [x] Experience feedback proposal、可复现 `.dlk` 与 Markdown/Obsidian 投影
- [ ] 为 `.dlk` 建立独立发布者签名、撤销和单调更新策略
- [x] 冻结十套件 held-out 协议、逐题统计、独立签名证据链与宣称门禁
- [ ] 完成十套件真实运行、两个第三方秘密留出与两家独立复现
- [ ] 扩展完整法律层级与双时态法律事件账本
- [ ] 增加 Corpus Coverage Manifest 与 release 审批/撤销元数据
- [ ] 建立外部 held-out 中文法律证据 benchmark
- [ ] 完成 Analytix turn-scoped 激活与 inactive zero-impact A/B gate

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/KNOWLEDGE_OS.md`](docs/KNOWLEDGE_OS.md) | Knowledge Asset、Context Capsule、记忆生命周期、包格式与安全边界 |
| [`docs/DEEPLAW_2.md`](docs/DEEPLAW_2.md) | Legal Pack 技术设计、形式化不变量与研究门禁 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 系统架构、存储与运行时事实 |
| [`docs/DOCUMENT_IR.md`](docs/DOCUMENT_IR.md) | DOCX/PDF/TXT 摄取、Document IR、PDF 多候选门禁与 Markdown 定位 |
| [`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md) | 法源、复核、许可、发布与更新治理 |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | 可复现验证结果与下一阶段评价协议 |
| [`docs/EXTERNAL_BENCHMARK_PROTOCOL.md`](docs/EXTERNAL_BENCHMARK_PROTOCOL.md) | 外部 held-out、公平对照、签名证据与宣称门禁 |
| [`docs/IMPLEMENTATION_AUDIT_2026-07-26.md`](docs/IMPLEMENTATION_AUDIT_2026-07-26.md) | 施工方案、源码、功能和剩余边界逐项复核 |
| [`docs/RESEARCH_MATRIX.md`](docs/RESEARCH_MATRIX.md) | Agent 知识库技术研究矩阵、分层边界与对照门禁 |
| [`docs/KNOWLEDGE_OS_RESEARCH.md`](docs/KNOWLEDGE_OS_RESEARCH.md) | 长期知识、记忆安全、隔离与 Context Capsule 的研究决策 |
| [`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md) | Codex、Claude Code 与 OpenCode 适配 |
| [`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md) | Analytix 未来接入与 zero-impact 门禁 |
| [`docs/SOURCE_AUDIT_2026-07-14.md`](docs/SOURCE_AUDIT_2026-07-14.md) | 首批 28 份资料的来源与构建历史审计 |

## 当前收录与更新

DeepLaw 2.0 是通用法律知识库。当前官方目录收录截至 **2026-07-14** 的 **28** 份资料，包括
**10 DOCX** 和 **18 PDF**；这只是当前覆盖情况，不限定未来法域和资料类型。仓库分发签名目录、
公钥信任根、来源 URL、大小和 hash，原件在安装时从官方来源获取。

| 当前法源分组 | 数量 | 覆盖内容 |
| --- | ---: | --- |
| 核心法源 | 4 | 刑法、刑诉法、修正案与立案追诉标准 |
| 金融与非法集资 | 4 | 洗钱、反洗钱、非法集资及取缔规则 |
| 数据与网络 | 3 | 个人信息、数据安全与反电信网络诈骗 |
| 案例参考 | 4 | 人民法院案例库公开案例 |
| 办案程序与证据 | 4 | 经侦办案、刑事程序与涉案财物处置 |
| 反洗钱、支付与主体穿透 | 8 | 外汇、受益所有人、客户尽调、支付机构等 |
| 罪名专题 | 1 | 危害税收征管刑事案件司法解释 |
| **合计** | **28** | **10 DOCX + 18 PDF** |

DeepLaw 2.0 分别记录**发布机关**与**官方托管下载站点**：前者用于识别来源权威，后者记录原件的
实际取得位置。

这里的“官方目录”指 DeepLaw 团队维护、签名并从下列官方站点取材的下载目录；它不表示发布机关
对 DeepLaw 构建结果作出认证，也不等同于逐条人工法律审查。

| 官方下载来源 | 数量 | 当前取得的文件 |
| --- | ---: | --- |
| [国家法律法规数据库](https://flk.npc.gov.cn/) | 10 | DOCX：法律、修正案、司法解释等 |
| [司法部行政法规库](https://xzfg.moj.gov.cn/) | 4 | PDF：行政法规及相关规范 |
| [中国人民银行](https://www.pbc.gov.cn/)及其官方分支站点 | 6 | PDF：反洗钱、支付、客户尽调与修改决定 |
| [山东法院官网](https://www.sdcourt.gov.cn/)官方托管页 | 5 | PDF：人民法院案例库参考案例及程序文件 |
| [证监会](https://www.csrc.gov.cn/)、[国家移民管理局](https://www.nia.gov.cn/)、[深交所](https://www.szse.cn/)官方托管页 | 3 | PDF：发布机关文件的官方托管原件 |
| **合计** | **28** | **每份均记录 URL、格式、字节数与 SHA-256** |

使用者获取团队更新只需显式运行：

```bash
deeplaw official update
```

团队维护目录时遵循三个步骤：

1. 以题名、文号、发布机关、公布/施行日期和时效状态定位资料；
2. 从发布机关或官方托管页面取得原件，不猜测下载 URL，也不把网页正文另存为原件；
3. 校验格式、首页身份、大小与 SHA-256，记录版本关系后构建新的不可变 release。

草案、征求意见稿、仅有网页正文的材料、商业数据库转载和案件私有材料不进入公共目录。案例
只用于检索与论证参考，不能替代法律规范的效力判断。

## 社区与许可

欢迎使用 synthetic fixture 提交可复现的定位、版本、解析和安全问题。参见
[`CONTRIBUTING.md`](CONTRIBUTING.md)、[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) 和
[`SECURITY.md`](SECURITY.md)。

DeepLaw 源代码按 [Apache License 2.0](LICENSE) 发布。外部法源、案例、网站版式和第三方资产的
相关权利仍由各权利人保留；可选文档处理依赖的许可、模型与再分发边界见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
