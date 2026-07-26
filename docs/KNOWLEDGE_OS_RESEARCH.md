# Knowledge OS 研究决策记录

状态：2026-07-26 复核；对应软件基线 `v0.4.0`。

本文记录 DeepLaw 2.0 通用 Knowledge Asset 核心的研究依据。它解释为什么当前实现选择
“来源保留、人工批准、显式生命周期、隔离 vault、有界 Context Capsule”，以及哪些能力
仍必须经过基准验证才能进入默认路径。本文是设计依据，不是当前能力证明；当前事实以代码、
测试和 [`KNOWLEDGE_OS.md`](KNOWLEDGE_OS.md) 为准。

## 研究问题

面向长期工作的 Agent，知识层至少需要同时回答：

1. 这条知识来自哪里，原始内容还能否回放？
2. 它在什么时间、项目、用户和任务范围内有效？
3. 谁批准了它，后来是否被替代、撤销或证明冲突？
4. 本次任务为什么选中它，消耗了多少上下文？
5. 外部资料、模型输出和工具结果能否污染后续任务？
6. 知识如何共享，同时不继承另一环境的信任和权限？

单纯提高相似度、关系数量或摘要层级不能同时解决这些问题。

## 上游研究带来的约束

| 研究或系统 | 可借鉴能力 | DeepLaw 采用的约束 | 没有直接采用的部分 |
| --- | --- | --- | --- |
| [Wiki Graph](https://github.com/oomol-lab/wiki-graph) | `.wikg` 章节树、source/summary/graph 分层、统一对象 URI、公共实体 QID 对齐、后台构建与 schema upgrade | 保留稳定 `deeplaw://` 对象句柄、来源与派生层分离、readiness/inspect、可移植包和有界 context | 不把自动实体/三元组或摘要写成已审核知识；不引入其 Node/LLM 作业运行时 |
| [Graphiti](https://github.com/getzep/graphiti) | episode provenance、事实有效期与摄取时间、混合检索 | source fragment 独立保留；Asset 有显式生命周期和替代关系 | 不把自动抽取图谱作为真源或默认检索路径 |
| [Letta](https://github.com/letta-ai/letta) | Agent state 与长期运行记忆 | DeepLaw 与宿主 session/runtime 分离，只保存值得跨任务复用的审核知识 | 不允许 Agent 直接改写长期知识 |
| [MemOS](https://github.com/MemTensor/MemOS) | 多 memory cube、隔离和组合 | 不同用户、项目和领域使用独立 vault；不在检索时混库 | 当前不建设远程多租户服务 |
| [Cognee](https://github.com/topoteretes/cognee) | 持久知识、检索与反馈闭环 | 保留 debugger/feedback，但反馈只能形成 proposal | 不开放 Agent-facing `remember`、`learn`、`forget` |
| [ACE](https://arxiv.org/abs/2510.04618) | 识别反复重写上下文可能产生 context collapse | 资产不可静默重写；替代和撤销均显式；Capsule 有硬预算 | 不把自动合并摘要写回长期真源 |
| [MemoryAgentBench](https://arxiv.org/abs/2507.05257) | 评估准确检索、测试时学习、长程理解和选择性遗忘 | 后续 benchmark 必须覆盖生命周期与选择性退出，而非只测命中率 | 单元测试不作为跨系统领先证据 |
| [LongMemEval](https://arxiv.org/abs/2410.10813) 与 [LongMemEval V2](https://arxiv.org/abs/2605.12493) | 长期记忆的抽取、时间推理、知识更新、拒答与主动回忆 | benchmark 增加 stale、contradiction、gap 和 irrelevant-context 指标 | 不用单一问答准确率代表长期知识质量 |
| [Memora](https://github.com/geniesinc/Memora) | 以周/月/季度跨度检验 remembering、forgetting、偏好变化和摊销成本 | 把 `forgetting_accuracy` 与旧偏好污染设为独立门禁 | 不把公开结果当作未见题 |
| [STATE-Bench](https://github.com/microsoft/STATE-Bench) | 用训练轨迹改善未见企业 Agent 任务 | 长期知识必须提高最终任务成功，而不只是召回片段 | 不把宿主 Agent 学习与 DeepLaw 自动写入混为一体 |
| [Agent Memory Benchmark](https://github.com/vectorize-io/agent-memory-benchmark) | 同时度量 Agent 任务、摄取、查询速度、Token 与成本 | 索引成本按相同注册查询量摊销；保留冷/热路径 | 不用单次热查询掩盖构建成本 |
| [Legal RAG Bench](https://github.com/isaacus-dev/legal-rag-bench) | 检查端到端法律检索和推理 | 中文秘密法律集也必须有 task success、来源和错误版本硬门禁 | 英文公开集不替代中国法时效金标 |
| [Agent Security Bench](https://github.com/agiresearch/ASB) | memory poisoning 与 observation injection | 投毒成功和越权修改成为独立零抵消指标 | 不用冻结 fixture 的人工激活结果证明安全 |
| [Bad Memory](https://arxiv.org/abs/2607.14611) | 持久记忆可成为跨会话 prompt-injection 通道 | 指令式/不可见文本触发 quarantine；MCP 只读；只有审核规则可成为指令候选 | 不允许外部内容自动进入 active memory |
| [OWASP Agent Memory Guard](https://owasp.org/www-project-agent-memory-guard/) | 记忆投毒、越权和审计风险需要独立控制面 | restricted 不进入 MCP；owner-only vault；hash-chain audit；导入降权 | 当前不宣称多租户认证或加密隔离 |
| [W3C PROV-O](https://www.w3.org/TR/prov-o/) | entity/activity/agent provenance 可互操作表达 | compiler、来源、fragment、review 和 mutation 分层记录 | v1 不引入完整 RDF/OWL 运行时 |

以上项目仅作为架构与评价协议参考；`v0.4.0` Knowledge Asset 核心没有复制这些仓库的
源代码。许可证和已审阅的代码复用边界见 [`UPSTREAM_REUSE.md`](UPSTREAM_REUSE.md) 与仓库
根目录的 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。

## 由研究导出的最终设计

### 1. 原件、fragment 与 Asset 三层不能合并

```text
source bytes
  -> stable fragment + locator + quote hash
  -> lifecycle-managed Knowledge Asset
```

原件负责复现，fragment 负责精确定位，Asset 负责治理后的可用知识。摘要、分类或关系即使
经过审核，也不能删除或覆盖其来源片段。

### 2. 长期知识写入必须经过控制面

Agent、网页、文件和工具结果都属于不可信输入。`ingest`、`debug` 和 `feedback` 只能创建
proposal；`approve`、`revoke`、`relate` 和 `import` 仅存在于人工管理 CLI。Agent MCP
没有任何写 operation。

### 3. 更新是替代，不是静默改写

同一 `semantic_key` 只允许一个 active Asset。新结论必须指明被替代 Asset；旧资产保留为
`superseded`，撤销保留为 `revoked`。working memory 强制过期。这样才能区分“过去正确”
“现在有效”和“已被明确否定”。

### 4. 隔离优先于检索后过滤

不同用户、项目和领域使用独立 owner-only vault。restricted Asset 永不进入 Agent MCP；
Analytix 案件附件、会话和案件数据库不进入 DeepLaw。应用层过滤不能替代物理数据边界。

### 5. Context Capsule 是有预算的编译产物

Context Compiler 只从 active、human-reviewed、未过期资产中选择，优先约束、决策、规则和
流程，并输出：

- 被选内容、来源和 reviewed relation；
- 条目/字符预算与被预算排除数量；
- 冲突、缺口、下一步；
- vault revision、历史 audit anchor 和全字段 digest。

Capsule 不是新的真源，也不是完整记忆转储。检索到的普通内容默认只作为数据；它不能覆盖
系统、开发者、仓库或当前用户指令。

### 6. 可迁移包默认不可信

`.dlk` v1 证明 payload 完整性，但没有发布者签名。即使 hash 正确，导入资产也统一降为
`untrusted + quarantined`，必须本地复核。发布者签名、撤销和单调更新完成前，不允许继承
远端 active 状态。

### 7. 图、语义索引与生成式解释都是可删除 sidecar

这些能力只有在相同语料、任务、模型和上下文预算下，显著提升任务成功率且不降低 provenance
coverage、隔离、投毒抵抗和 gap 识别时，才能进入默认路径。它们不能成为唯一真源，也不能
绕过审核状态。

## Wiki Graph 专项复核

2026-07-25 复核了 `oomol-lab/wiki-graph` 的
`7f916f63cfb9df1f5361001167c92a7a7fef2146` 快照。它有五项当前明显强于
DeepLaw `v0.4.0` 的工程能力：

1. `.wikg` 公开标准明确保存 chapter tree、source stream、summary stream、结构化数据库和
   可选全文索引，长材料的层级导航比 DeepLaw 当前的线性 fragment locator 更完整；
2. `wikg://` 将 scope、object 和 predicate 统一成可发现的 CLI/SDK 对象协议，适合 Agent
   逐层探索，而不是记忆大量互不关联的命令；
3. public entity 先经 Wiki/Wikidata QID grounding，再生成 evidence-bound triple，跨书籍
   聚合同一公共实体的能力比 DeepLaw 当前人工关系更丰富；
4. build queue、成本估算、watch、staging、archive coordinator、library registry 和 GC
   已形成大语料异步控制面；
5. archive/home schema gate 与相邻版本 upgrader 有明确失败恢复规则，成熟度高于 DeepLaw
   当前只支持 storage schema v1 的做法。

但这些优势不能直接替代 DeepLaw 的规范层：

- `.wikg` mutation token 用于变更和缓存协调，不是整个归档的内容摘要或发布者签名；
- Knowledge Graph 的实体消歧、关系和摘要仍含模型生成步骤，证据 quote 的自动匹配阈值只能
  作为候选定位，不能自动获得 `human_verified`；
- `pack --budget` 的文本输出最终截断有实用价值，但 DeepLaw 需要对结构化 MCP/JSON 输出、
  来源元数据和完整序列化载荷同时执行预算与摘要校验；
- public QID 对私有项目决策、内部术语、未公开经验和中国法条版本身份覆盖有限；
- 其 Agent/CLI 能发起写入和 LLM build job，不能直接成为 DeepLaw 的只读宿主接口。

因此采用“吸收协议，不替换可信内核”的路线：

- 当前保留 DeepLaw 已实现的对象 URI、source/derived 分层、SQLite 规范存储、readiness
  inspection、`.dlk` 和 Capsule；
- 下一 storage schema 先设计可回放的 source hierarchy，再考虑 library registry 和统一
  URI discovery；
- 长材料 Reading Graph、公共实体 grounding 和 triple extraction 只作为可删除 sidecar，
  产物进入 proposal/quarantine，必须通过同预算 held-out benchmark 与人工复核；
- 在引入后台编译前，先完成相邻 schema migration、作业租约、失败恢复、成本预估和取消语义，
  不把这些复杂度放入只读 MCP。

本次复核没有复制 Wiki Graph 源码。具体许可和快照记录见
[`UPSTREAM_REUSE.md`](UPSTREAM_REUSE.md)。

## 评价协议

冻结协议见
[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md)，同时报告：

| 维度 | 最低要求 |
| --- | --- |
| task success | 完成任务且遵守已审核约束 |
| useful-context recall | 必要知识进入 Capsule |
| irrelevant-context rate | 无关字符占比可见且受控 |
| provenance coverage | 每个来源型结论可回链 fragment |
| update handling | 能识别 superseded、revoked、expired 和 stale |
| contradiction handling | 不静默选择冲突结论，输出 gap |
| forgetting | 明确撤销、过期和退出知识不再影响任务 |
| poisoning resistance | 恶意资料不能获得 active/directive 权限 |
| unauthorized mutation | Agent 不能越过只读接口修改 vault 或 Legal Pack |
| isolation | 跨 vault、restricted 和案件资料零泄漏 |
| budget | Capsule 条目、字符、延迟、内存、索引和摊销成本 |
| abstention | 缺少证据时明确拒绝编造 |

对照实验必须固定 corpus、问题、模型、prompt、工具权限和预算，公开失败样本。十套件真实
运行、两个秘密 held-out 和两家独立签名复现尚未完成。只有机器 claim gate 通过，才能声称
某种 sidecar 或策略相对列明基线更好；“功能更多”和单元测试通过都不构成领先证据。

10 万资产合成诊断只建立当前本地 vault 的已测工作区间。它证明尾部实体、Capsule、审计重放
和常驻读路径在该规模下按契约工作，但唯一标识查询不能替代自然语言语义泛化。当前公开开发
集仍显示偏好与改写召回不足，所以语义 discovery 只能以来源绑定、可删除、版本化 sidecar
进入下一轮外部评测；没有 held-out 净收益前不进入默认运行时。

## 当前结论

DeepLaw 2.0 的可辩护创新不是发明另一个不可解释的召回算法，而是把知识变成具有来源、
权限、生命周期、审核、上下文预算和可迁移边界的编译资产。`v0.4.0` 实现了这条最小可信
闭环；自动长期学习、远程多租户、`.dlk` 发布者签名和跨系统质量领先仍是待验证能力。

## 第三次发布级复核形成的实现约束

施工后攻击性 CLI 复核确认了三个不能只靠文档约定的风险：直接改 SQLite 生命周期字段、
用户自封 `verified_source`、以及只凭形似合法的 Capsule ID 写反馈。`v0.4.0` 当前实现已将
它们分别关闭为：

1. event chain 与当前 Asset/source/fragment/relation/FTS 全量重放核对，数据库身份变化即
   失效缓存并重新验证；
2. `verified_source` 从用户 CLI 与 store API 移除，通用 Asset 固定
   `legal_authority=false`；
3. feedback 必须验证真实 Capsule 文件、当前 vault、audit anchor 和所选 Asset；
4. 手工 proposal 与文件摄取共享 prompt-injection 检测，quarantine 激活需要第二个确认；
5. 所选 source-bound Asset 在 search/get/context/export 路径检查原件 hash；
6. Context 只允许一次有界、人工复核关系扩展，并把选择原因写入 Capsule；问题正文不能直接
   变成 Agent action；
7. CLI 与 MCP 的 Context task/goal 需要显式非案件数据声明；MCP 未配置 vault 时仍能完成
   工具发现，任何读取都以不泄漏路径的错误失败关闭；
8. Context 按剩余条目公平分配字符、围绕查询位置取可重放 excerpt，并限制同源 part 拥挤；
9. Legal Pack derivation 绑定 review governance、collection scope 和 build-report 身份；
10. 外部结果以签名 suite manifest 绑定逐题报告、比较、资源与运行配置，并实际执行
    Holm–Bonferroni；无贡献的签名机构不计入独立评测方。

这些约束提高的是可验证性、无关上下文控制和持久记忆安全，不构成对所有外部知识系统的
通用质量领先证明。跨系统领先仍必须通过前述 held-out 协议获得证据。
