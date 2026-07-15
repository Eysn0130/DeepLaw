# DeepLaw 2.0 技术设计：面向 Agent 的可验证知识库

Status: current architecture and research roadmap, 2026-07-16.

> **Files in. Verifiable knowledge out.**
> 文件进入，Agent 得到可验证的知识。

`DeepLaw 2.0` 是产品名；仓库名保持 `DeepLaw`，Python 包、CLI 和本地目录保持 `deeplaw`。
当前软件版本是 `v0.3.0`。本文严格区分已经存在的能力与后续研究目标，架构本身不使用版本号。

## DeepLaw 是什么

DeepLaw 2.0 是面向 Agent 的法律知识库。它将 DOCX、PDF、TXT 文件处理为只读、版本化、
可追溯的 Knowledge Release，并向 Agent 交付小型 Evidence Pack；当前 `v0.3.0` 官方团队
目录输入为 DOCX/PDF，物理分离的用户私有法律参考库另支持 UTF-8 TXT。

DeepLaw 不是聊天记忆，不保存案件项目私有资料，也不把整座知识库塞进模型上下文。用户私有
DeepLaw 范围只保存法律参考资料，始终标记为用户提供且未经官方审核。它解决四个
问题：

1. 文件中的知识如何稳定进入知识库；
2. Agent 如何在大量知识中定位、连接和解释需要的内容；
3. 哪些内容满足当前问题的证据要求；
4. 哪些地方仍然缺失、不确定或不能使用。

官方目录、用户私有法律参考与 Analytix 案件项目必须物理隔离。当前 `v0.3.0` 不提供内容级
DLP 或案件私有资料分类器；宿主必须在调用或导入 DeepLaw 前完成隔离和拒绝，不能依赖
DeepLaw 自动识别误传内容。

## 五秒架构

```text
Files
  ↓
DeepLaw Knowledge Base
  ↓
Locate · Connect · Explain
  ↓
Evidence Pack
  ↓
Agent
```

这条主路径必须保持简单。复杂能力放在 Evidence Core 内部，不暴露成一长串 Agent 工具。

## 六个知识动作

DeepLaw 架构用六个原生动作描述自己的能力：

| 动作 | 作用 | 不能做什么 |
| --- | --- | --- |
| `Ingest` | 校验来源、解析文件、保留页码/段落/hash | 不把处理成功当成人工审核通过 |
| `Organize` | 构建文档层级、版本、关系和 Knowledge Map | 不让生成摘要覆盖原文 |
| `Locate` | 精确定位题名、文号、条款、关键词和相关片段 | 不因主题相似就无限返回 |
| `Connect` | 发现定义、引用、修订、废止、替代和限制关系 | 不把图连接当成法律结论 |
| `Explain` | 生成有来源的导航、短摘要和问题分解 | 不让派生解释成为权威来源 |
| `Verify` | 执行来源、时效、证据义务、预算、缺口和回执 | 不为看起来完整而补造答案 |

`Deliver` 是最终交付动作：Agent 只得到被选中的 Evidence Pack，而不是内部候选池。

## 四个核心数据对象

### 1. Knowledge Release

一个不可变、内容寻址的知识库发布物，至少绑定：

- source manifest 与文件 SHA-256；
- 文档身份、来源 URL、机关、文号和版本；
- 页级或段落级定位；
- extraction backend、配置、风险和 review status；
- 每个 segment 的稳定 ID 与 text hash；
- Knowledge Map 的来源关系；
- release ID、database hash 和 schema version。

运行时以 SQLite `mode=ro&immutable=1` 打开固定 release。构建、审核、激活和查询是不同权限
面；查询接口不能修改语料。

### 2. Knowledge Map

Knowledge Map 不是一张可以自由猜测关系的图。它包含两类严格隔离的信息：

- **Authority Map**：进入 release、绑定精确来源 segment、hash 和 review status 的关系；
- **Discovery Map**：由语义、统计或模型发现的候选关系，可随时删除和重建。

Discovery Map 只能提出 `segment_id`。它不能改变来源身份、时效、审核状态或 receipt，也不能
直接覆盖 Evidence Duty。

### 四阶段不等式

```text
Discovery != Admission != Selection != Adjudication
```

- **Discovery** 只提出带 `source_id` / `segment_id` 的候选；词法、语义、树、图、Wiki、模型或
  reranker 都只能位于这一层。
- **Admission** 只读取确定性的完整性、来源、版本/时点、抽取 provenance/risk 与复核状态；
  任何模型分数、摘要、投票、求解器或派生关系都不能提升这些能力。
- **Selection** 在已分桶候选上按 Duty、卡片和字符预算做确定性覆盖选择；选择不是法律充分性
  证明，未覆盖要求必须成为 gap。
- **Adjudication** 不属于 DeepLaw。案件事实认定、规则适用和裁判结论留给有权限的使用者与
  法律审查；Agent 不能因 receipt 有效就跳过 blocking gap。

`v0.3.0` 已实现固定 release、integrity/抽取门禁、显式时点查询分桶、coverage-first 选择和
Skill 级 blocking-gap 检查；完整逐来源人工准入 capability 与宿主不可绕过的 final gate 仍是
后续工作，因此本文不把当前实现描述为完整的四阶段形式化证明系统。

### 3. Evidence Duty

Agent 的问题先被编译成一个封闭的证据任务，而不是直接取前几条结果。当前和规划中的 Duty
包括：

- primary rule；
- exact citation；
- temporal status / version；
- elements / definitions；
- interpretation；
- procedure；
- exceptions / counterevidence；
- case reference。

每个 Duty 声明自己需要什么类型的证据，以及没有覆盖时是否阻断回答。

### 4. Evidence Pack

Evidence Pack 是 Agent 实际看到的有界结果。`v0.3.0` 已有 `query_plan`、
`evidence_compilation`、`evidence`、`uncertain_evidence`、`obligation_coverage` 与 `gaps`；
`receipt_id` 位于每张 evidence card 内。更完整的顶层 `receipt_ids` 和执行轨迹读取接口仍是
后续目标：

```text
EvidencePack {
  release_id
  query_plan
  evidence[]              # 主证据
  uncertain_evidence[]    # 不满足主证据门禁
  obligation_coverage[]
  gaps[]
  receipt_ids[]
  trace_hash
}
```

默认最多五张卡；所选 segment 的规范化抽取文本按精确 `segment_id` 二次读取。若
`truncated=true`，可在契约上限 6000 字符内提高 `max_chars` 后重取；抽取文本仍须按 official
source 与 locator 核对。这样，大规模筛选发生在模型上下文之外。

## Evidence Core

Evidence Core 是 DeepLaw 架构的技术核心，由五个相互约束的模块组成。

### Sources & Versions

- 校验 release、source、segment 和 database hash；
- 识别文件、机关、文号、别名和版本；
- 区分公布、施行、修订、失效、废止、替代和历史状态；
- 用户显式提供 `as_of` 时，未经验证的时效元数据只能进入 uncertain；未提出时点问题时，
  `v0.3.0` 不把返回结果表述为“已验证现行有效”。

### Knowledge Map

- 保留文件原生层级；
- 建立有来源的引用、修订、废止、替代和实施关系；
- 把派生关系限制在 Discovery Map；
- 限制路径数量、方向和 hop，防止关系扩散占满上下文。

### Evidence Duties

- 将问题编译成稳定 QueryPlan；
- 为候选生成 coverage witness；
- 主动寻找定义、限制、例外、反证和版本变化；
- 没有 witness 的候选不能把 Duty 标记为 `covered`。

### Limits & Gaps

- 卡片数、字符数、图路径和 hop 有硬上限；
- 重复主题片段不能挤掉精确条文或必要反证；
- 区分 evidence、corpus、review、temporal 和 extraction gap；
- “没有足够证据”不能被解释为“知识不存在”。

### Receipts & Replay

- receipt 绑定 release、document、segment、source hash 和 text hash；
- 当前编译结果记录 candidate/result digest、逐项增量 witness 和汇总拒绝原因；
- 相同 release、QueryPlan 与确定性策略产生相同 selected IDs、gaps 和 result digest；
- 完整逐候选 execution trace、trace hash 与按 ID 读取接口属于后续目标，`v0.3.0` 不作此声明。

## 核心创新一：先定义证据要求，再选择内容

常见知识库先返回最高分结果，再问“够不够”。DeepLaw 先编译启发式 Evidence Duties，再在硬预算内
选择能新增或改善 witness 的小型去冗余覆盖集：

```text
Question
  → closed QueryPlan
  → bounded candidate discovery
  → integrity / relevance / temporal-intent / extraction admission
  → coverage witnesses
  → limitation and counterevidence challenges
  → bounded coverage-first evidence set
  → evidence + uncertain + gaps + receipts
```

当前 `v0.3.0` 已实现 QueryPlan、封闭 Evidence Duties、相关性准入、coverage-first 选择、
coverage witness、候选/结果 digest、严格时效分桶、gaps 和 receipt。更完整的 challenge 状态与
可按 ID 读取的执行轨迹仍是后续增量。

对于已审核的法律概念，DeepLaw 还可把概念绑定到“题名 + 原件 SHA-256 + 条款”的封闭 locator
集合，并在普通全文候选池之外注入这些 locator。主题门先于证据选择：相邻罪名或相似标准不能
替代查询主题；同一张卡片没有同时通过主题/问题 witness 时，不能替该问题覆盖实体义务。主题
无法解析时返回 blocking `query_focus_unresolved`，而不是选择候选池中“最不差”的错误片段。
来源绑定主题的身份门不依赖宿主选择 `navigation` 还是 `research` 路由；同一短主题经不同宿主
适配器进入时，必须解析到同一主 locator，支持 locator 只能见证它被登记承担的独立证据义务。

## 核心创新二：证据能力类型

DeepLaw 不允许发现排序提升时效、抽取、完整性或审核状态。下列完整 capability type 是目标模型；
`v0.3.0` 仍以显式 temporal classification、抽取标记和 compiler 的 uncertain boolean 投影其中一部分：

```text
EvidenceCapabilities {
  integrity: verified | failed
  source_identity: reviewed | declared | unknown
  authority_metadata: reviewed | heuristic | unknown
  temporal: verified_at(date) | unknown | outside
  extraction: native_reviewed | ocr_human_reviewed | ocr_unreviewed | warned
  provenance: exact_segment | derived
}
```

例如，历史时点问题不仅需要文本相关，还要求 `temporal=verified_at(target_date)`；派生解释在
解析到 exact segment 之前不具备主证据能力。模型分数不能提升这些类型。

## 核心创新三：限制与反证是第一等结果

对每张候选，DeepLaw 执行有限挑战：

- `temporal_challenge`：是否修订、废止、替代或尚未施行？
- `exception_challenge`：是否存在但书、除外、不适用或另有规定？
- `definition_challenge`：关键术语是否在其他位置定义？
- `scope_challenge`：地域、机关、主体和事项范围是否一致？
- `cross_reference_challenge`：是否遗漏附件、解释或实施规则？
- `extraction_challenge`：OCR、表格或版式是否可能改变关键字符？
- `conflict_challenge`：是否存在特殊规则或更高效力来源？

状态只能是 `satisfied`、`unresolved` 或 `not_applicable`。`unresolved` 形成 gap，不由模型自行
补齐。

## 核心创新四：有界、覆盖优先的去冗余选择

当前实现把 Admission 后候选限制为 `N <= 256`，输出 `K <= 5`，并以确定性的逐步选择器执行：

1. 按 Duty 声明顺序优先处理必需义务，精确 `query_focus` 位于最前；
2. 只有能新增 witness，或把 uncertain witness 改善为 admissible witness 的候选才可进入结果；
3. 同一阶段先选已通过门禁的候选，只有它们无法改善覆盖时才使用 uncertain fallback；
4. 在同一 Duty 优先级内，依次考虑新增覆盖、文档多样性、检索相关性、来源权威、字符数与
   稳定 `candidate_id`；
5. 字符预算和卡片预算耗尽即停止，未覆盖必需 Duty 形成显式 gap。

这是可重放的 coverage-first 贪心选择，不是全局集合覆盖最优解，也不宣称“数学上最小”。它的
产品目标是阻止重复主题片段挤掉精确条文或必要限制，同时让每个返回项都有可检查的增量理由。

## 核心创新五：法律双时态事件账本

单一 `effective_from/effective_to` 无法安全表达修订链。DeepLaw 架构区分：

- `valid_time`：规则在法律世界中的效力时间；
- `record_time`：DeepLaw 在哪个 release 中获知并审核该事件。

最小事件集合包括 `promulgates`、`effective`、`amends`、`corrects`、`repeals`、
`suspends`、`revives`、`supersedes` 和 `implements`。每个事件绑定触发法源、精确 segment、
hash、日期、影响范围和 review status。

只有事件链完整且已审核时，系统才能确定性重建目标时点；否则返回历史原文和 temporal gap，
而不是让模型拼接“当时版本”。

## Corpus Coverage Manifest

每个 release 必须声明：

```text
jurisdictions
source_classes
document_families
reviewed_through
known_exclusions
temporal_coverage
extraction_review_coverage
redistribution_status
```

查询结果据此区分：

- `evidence_gap`：候选存在但不满足 Duty；
- `corpus_gap`：语料没有覆盖该领域；
- `review_gap`：材料存在但未审核；
- `temporal_gap`：事件链不完整；
- `extraction_gap`：OCR 或版式未确认。

## 形式化不变量

以下要求应成为 contract/property tests：

1. **Provenance integrity**：所有主证据解析到当前 release 的唯一 segment，hash 全部匹配。
2. **No authority escalation**：派生索引、模型和排序器不能提高权威、时效或审核状态。
3. **Temporal admission**：`as_of=t` 时，未知时效只能进入 uncertain，区间外不得进入主证据。
4. **Extraction admission**：未关闭抽取复核标记的候选只能进入 uncertain，不能成为主证据。
5. **Coverage witness**：没有通过 capability predicate 的 witness 就不能标记 `covered`。
6. **Map non-entailment**：关系路径只能提出候选或 challenge，不能单独覆盖法律 Duty。
7. **Bounded context**：任何插件都不能突破卡片、字符、路径和 hop 上限。
8. **Deterministic replay**：相同输入和版本产生相同 IDs、gaps 和 result digest。
9. **Exact stability**：添加无关文档或派生索引不能挤掉有效精确匹配。
10. **Gap monotonicity**：低信任或 derived 材料不能消除 blocking gap。
11. **Scope honesty**：无结果只描述声明范围，不能断言知识不存在。
12. **Scope isolation**：官方、用户私有法律参考、案件项目物理分离；日志和 benchmark 不持久化案件事实。
13. **Model non-interference**：替换模型可改变发现顺序，不能改变权威、时效、hash 或 receipt。
14. **Topic non-substitution**：相邻概念、复合罪名或其他法条的标准不得替代当前查询主题；未解析
    时必须保留 blocking gap。

## 当前实现与后续目标

| 能力 | `v0.3.0` 当前实现 | 后续目标 |
| --- | --- | --- |
| Ingest | 官方 DOCX/PDF、私有 UTF-8 TXT、Document IR、页级与 segment 级抽取证据、content-addressed SQLite | 官方 TXT、历史 DOC 受控转换与完整 Corpus Coverage Manifest |
| Organize | 标题/条文分段、顺序、稳定 segment、来源关系 | 完整法律层级、双时态事件账本和双 Map 隔离 |
| Locate | 精确题名/文号/条款、中文 FTS、来源 hash 绑定的法律主题 locator 与失败关闭主题门 | 可插拔本地发现 sidecar |
| Connect | provenance-bound 单跳关系 | 有限 challenge closure |
| Explain | 有界 excerpt 与 next questions | release-pinned、source-bound navigation |
| Verify | 时效分桶、gaps、receipt、coverage witness、候选/结果 digest | 完整 capability types 与可读取 replay trace |
| Select | 相关性准入后执行有界、coverage-first、去冗余的确定性选择 | challenge-aware 组合优化与外部留出集校准 |
| Agent | Skill 与单一只读 MCP；官方/私有 operation 分离 | host schema 物化前显式激活 |

## 如何证明技术领先

DeepLaw 不用口号证明自己。外部主张需要在相同语料、问题、回答模型和上下文预算下，比较：

- 纯词法定位；
- 纯语义定位；
- 混合定位；
- 关系增强定位；
- 结构树导航；
- 派生摘要导航；
- DeepLaw 各阶段 ablation。

核心指标：

- evidence Recall@k、MRR、nDCG 与字符级 precision/recall；
- `AdmissibleRecall@Budget`；
- `FalseAuthorityAdmissionRate`；
- `TemporalFalseInclusionRate` / `TemporalFalseExclusionRate`；
- `ExceptionRecall`；
- `BlockingGapPrecision` / `BlockingGapRecall`；
- `ExactDisplacementRate`；
- `ReceiptVerificationRate`；
- `TraceReplayConsistency`；
- `RelevantChars / TotalContextChars`；
- `NonLegalActivationRate`；
- indexing/query cost 和 p50/p95 latency。

只有在外部 held-out 数据、专家中文法律集、mutation suite 和 inactive-host gate 同时通过后，
主页才可以发布领先性结论。当前 37 项白盒 smoke set（含 4 项主题错召回负例）只证明已编码行为
没有回归。

## 实现顺序

### Phase 0：冻结评价协议

建立 held-out、mutation、OCR critical-token 和 inactive-host benchmark。

### Phase 1：Challenge trace 与不变量补全

- 增加明确的 challenge result 状态；
- 把当前 selection、witness 与 digest 扩展为可读取的 execution trace；
- 在现有确定性与 mutation tests 之上补齐 property-based invariants。

### Phase 2：Evidence Capability Types

把 temporal、extraction、review、source hash 和 authority 提升为正交能力与 Duty predicate。

### Phase 3：Knowledge Event Ledger

实现最小 Work / Expression / Manifestation / LegalEvent 模型与 coverage manifest，不先构建庞大
法律本体。

### Phase 4：Input, Hierarchy And Discovery Sidecars

- 把已用于私有库的确定性 TXT extractor 纳入官方治理流程，并保留 locator、hash 和抽取证据；
- 把当前标题/条文分段扩展为可校验的完整法律层级；
- 按实验逐一增加本地语义发现、Discovery Map 和 source-bound Explain；
- 每个 sidecar 必须固定到 release、可删除重建，并有 ablation；没有净增益就不进入默认路径。

### Phase 5：Answer citation audit

检查引用 ID、逐字引文、locator、日期/版本陈述和 claim-to-evidence 绑定。语义蕴含只能标记
`model_assessed`，不能伪装成确定性证明。

### Phase 6：签名、撤销和安全更新

已落地第一步：bundled/HTTPS 官方 catalog 使用 Ed25519 分离签名、随包公钥信任根、精确字节
验签和 sequence 防 rollback/改写，网络目录不能绕过。后续继续使用成熟供应链规范记录
source → extraction → review → release，增加独立 release 审批签名、在线撤销/supersession、
freeze 与 mix-and-match 防护；不把目录签名冒充完整供应链，也不自创密码学原语。

## 明确不能做

- 不能让派生解释覆盖原文。
- 不能把关系连接当法律蕴含。
- 不能把相似度与法律效力混成一个总分。
- 不能在查询时把公网网页直接加入主证据。
- 不能由模型自行认定修订、废止、冲突或优先级。
- 不能无限多跳遍历。
- 不能用模型 judge 单独认证法律正确性。
- 不能把“无结果”表述成“知识不存在”。
- 不能默认把案件事实发送给远程服务。
- 不能把案件私有事实写入公共 release、sidecar、日志或公开 benchmark。
- 不能让派生 sidecar 脱离 release ID。
- 不能让 Agent 修改已发布的公共 Knowledge Release。
- 不能把 validity 判断写成 applicability 或裁判结论。
- 不能用白盒 smoke set 宣称超过所有系统。

## 最终原则

```text
Files establish the source.
Releases freeze the knowledge.
Maps connect what is known.
Duties define what is needed.
Challenges look for what could be wrong.
Evidence Packs bound the context.
Receipts bind what the agent used.
Gaps preserve what is still unknown.
```

这就是 DeepLaw 2.0：不是更大的上下文，而是一个让 Agent 可以定位、连接、解释
并验证知识的完整知识库。
