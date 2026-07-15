# DeepLaw Architecture 2.0：全新的 Agent 知识库

Status: architecture target and research plan, 2026-07-15.

> **Files in. Verifiable knowledge out.**
> 文件进入，Agent 得到可验证的知识。

`DeepLaw` 是产品名，Architecture 2.0 是下一代架构方向，不是当前 Python 包版本号。当前可
运行实现仍是 `0.3.0` alpha；本文严格区分已经存在的能力与后续研究目标。

## DeepLaw 是什么

DeepLaw 是面向 Agent 的法律知识库。Architecture 2.0 目标支持 DOCX、PDF、TXT 等文件，处理为
只读、版本化、可追溯的 Knowledge Release，并向 Agent 交付小型 Evidence Pack；当前
`0.3.0` 官方团队目录输入为 DOCX/PDF，物理分离的用户私有法律参考库另支持 UTF-8 TXT。

DeepLaw 不是聊天记忆，不保存案件项目私有资料，也不把整座知识库塞进模型上下文。用户私有
DeepLaw 范围只保存法律参考资料，始终标记为用户提供且未经官方审核。它解决四个
问题：

1. 文件中的知识如何稳定进入知识库；
2. Agent 如何在大量知识中定位、连接和解释需要的内容；
3. 哪些内容满足当前问题的证据要求；
4. 哪些地方仍然缺失、不确定或不能使用。

官方目录、用户私有法律参考与 Analytix 案件项目必须物理隔离。当前 `0.3.0` 不提供内容级
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

Architecture 2.0 用六个原生动作描述自己的能力：

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

Evidence Pack 是 Agent 实际看到的有界结果。下列是 Architecture 2.0 目标合同；`0.3.0` 已有
`query_plan`、`evidence`、`uncertain_evidence`、`obligation_coverage` 与 `gaps`，`receipt_id`
位于每张 evidence card 内。顶层 `receipt_ids`、`trace_hash`，以及选择前 coverage witness 均为
2.0 目标：

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

Evidence Core 是 Architecture 2.0 的技术核心，由五个相互约束的模块组成。

### Sources & Versions

- 校验 release、source、segment 和 database hash；
- 识别文件、机关、文号、别名和版本；
- 区分公布、施行、修订、失效、废止、替代和历史状态；
- 未经审核的时效元数据不能进入已验证主证据。

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
- execution trace 记录候选来源、准入/拒绝原因、coverage、选择和预算；
- 相同 release、plan、policy 和 engine 产生相同 selected IDs、gaps 和 trace hash；
- trace 默认不进入模型上下文，审计时按 ID 读取。

## 核心创新一：先定义充分，再选择内容

常见知识库先返回最高分结果，再问“够不够”。DeepLaw 先编译 Evidence Duties，再选择能完成
Duty 的最小证据集：

```text
Question
  → closed QueryPlan
  → bounded candidate discovery
  → source / version / extraction admission
  → coverage witnesses
  → limitation and counterevidence challenges
  → minimal sufficient evidence set
  → evidence + uncertain + gaps + receipts
```

当前 `0.3.0` 已实现 QueryPlan、八类 Duty、严格时效分桶、事后 coverage、gaps 和 receipt。
2.0 的关键增量是把选择顺序改为 coverage-first，并使每个 `covered` 状态携带机器可检查的
witness。

## 核心创新二：证据能力类型

DeepLaw 不把证据质量压成一个 `confidence=0.87`。不同风险必须保持正交：

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

## 核心创新四：最小充分证据集

Admission 后候选限制为 `N <= 40`，输出 `K <= 5`。在这个规模下，可以确定性枚举至多五项
组合，无需不透明优化器。目标按字典序最小化：

1. 未解析的精确目标；
2. 必需 blocking gaps；
3. 未解决的 challenge；
4. 只有 uncertain evidence 的必需 Duty；
5. 更弱的 source/temporal/extraction tier；
6. 上下文字符；
7. 卡片数量；
8. 稳定排序后的 segment IDs。

因此，加入大量同主题高分片段也不能挤掉精确条文、时效证据或例外规则。

## 核心创新五：法律双时态事件账本

单一 `effective_from/effective_to` 无法安全表达修订链。Architecture 2.0 区分：

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
8. **Deterministic replay**：相同输入和版本产生相同 IDs、gaps 和 trace hash。
9. **Exact stability**：添加无关文档或派生索引不能挤掉有效精确匹配。
10. **Gap monotonicity**：低信任或 derived 材料不能消除 blocking gap。
11. **Scope honesty**：无结果只描述声明范围，不能断言知识不存在。
11. **Scope isolation**：官方、用户私有法律参考、案件项目物理分离；日志和 benchmark 不持久化案件事实。
12. **Model non-interference**：替换模型可改变发现顺序，不能改变权威、时效、hash 或 receipt。

## 当前实现与 2.0 目标

| 能力 | `0.3.0` 当前实现 | Architecture 2.0 目标 |
| --- | --- | --- |
| Ingest | 官方 DOCX/PDF、私有 UTF-8 TXT、页级证据、content-addressed SQLite | 官方 TXT 与完整 Corpus Coverage Manifest |
| Organize | 标题/条文分段、顺序、稳定 segment、来源关系 | 完整法律层级、双时态事件账本和双 Map 隔离 |
| Locate | 精确题名/文号/条款、中文 FTS | 可插拔本地发现 sidecar |
| Connect | provenance-bound 单跳关系 | 有限 challenge closure |
| Explain | 有界 excerpt 与 next questions | release-pinned、source-bound navigation |
| Verify | 时效分桶、gaps、receipt | capability types、witness、replay trace |
| Select | 排序后截取，再计算 coverage | coverage-first 最小充分证据集 |
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
主页才可以发布领先性结论。当前 32 项白盒 smoke set 只证明已编码行为没有回归。

## 实现顺序

### Phase 0：冻结评价协议

建立 held-out、mutation、OCR critical-token 和 inactive-host benchmark。

### Phase 1：Evidence Selection v2

- coverage witness；
- challenge result；
- coverage-first selector；
- execution trace；
- property-based invariants。

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

使用成熟供应链规范记录 source → extraction → review → release，防止 rollback、freeze 和
mix-and-match；不自创密码学协议。

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

这就是 DeepLaw Architecture 2.0：不是更大的上下文，而是一个让 Agent 可以定位、连接、解释
并验证知识的完整知识库。
