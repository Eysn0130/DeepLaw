# DeepLaw 2.0 外部证据与宣称协议

## 结论先行

“全面超过所有知识库”不是一个可以被科学证明的命题：

- “所有”包含尚未公开、未来出现和无法取得的系统；
- 知识库、长期记忆、法律检索、上下文编排和安全系统的任务边界不同；
- 单一平均分可以用高召回掩盖噪声、错误版本、无来源内容、污染成功或高成本；
- 公开测试集一旦被开发团队看过，就不能继续冒充秘密留出集。

DeepLaw 永久拒绝无边界的世界第一或全面领先文案。可以成立的最高等级表述是：

> DeepLaw 2.0 `<version>` 在 `<protocol>` 的固定语料、模型、上下文预算和统计协议下，
> 相对列明的 `<n>` 个基线，在 `<m>` 个外部评测套件上通过了预注册门禁。

这句话只能由
[`benchmarks/external/claim_gate.py`](../benchmarks/external/claim_gate.py)
根据完整证据生成，不能由维护者手写。

## 四级证据

| 等级 | 含义 | 可以说明什么 | 不能说明什么 |
| --- | --- | --- | --- |
| L0 本仓 smoke | 已知回归题、单元测试、攻击性测试 | 实现与契约没有已知回归 | 跨系统质量 |
| L1 外部公开冻结集 | 在协议冻结后只运行、不调参的外部数据 | 对具名公开套件的可复现结果 | 真正秘密留出、独立性 |
| L2 第三方 held-out | 标签和测试实例从未交给 DeepLaw 开发团队 | 未见题条件下的泛化与安全结果 | 对未参评系统的结论 |
| L3 独立复现 | 至少两个独立机构复现固定产物与结果 | 对协议内具名基线的强证据 | “超过所有系统” |

对外性能主张必须同时达到 L1、L2 和 L3。L0 永远不能升级成领先证据。

## 已冻结的外部组合

当前机器协议位于
[`benchmarks/external/protocol-v2.json`](../benchmarks/external/protocol-v2.json)；
`protocol-v1.json` 只保留为历史记录，claim gate 会明确拒绝它。2026-07-26 冻结的
v2 组合不是围绕 DeepLaw 自建，而是覆盖互补能力：

| 套件 | 固定上游 | 主要检查 | 证据角色 |
| --- | --- | --- | --- |
| LongMemEval-V2 | `xiaowu0162/LongMemEval-V2@6f020ac2` | 静态/动态状态、流程、环境陷阱、错误前提、任务成功与延迟 | 外部公开冻结 |
| MemoryAgentBench | `HUST-AI-HYZ/MemoryAgentBench@455306dc` | 准确检索、测试时学习、长程理解、冲突解决 | 外部公开冻结 |
| Memora | `geniesinc/Memora@a6493188` | 长周期 remembering/forgetting、偏好更新和成本 | 外部公开冻结 |
| STATE-Bench Agent Learning | `microsoft/STATE-Bench@4efcbf2d` | 从训练轨迹学习后完成未见企业任务 | 外部公开冻结 |
| Agent Memory Benchmark | `vectorize-io/agent-memory-benchmark@aa9273ab` | Agent 任务准确率、摄取/查询速度、上下文和成本 | 外部公开冻结 |
| LegalBench-RAG | `zeroentropy-ai/legalbenchrag@431bc8f2` | 法律合同片段的字符级 precision/recall | 外部公开冻结 |
| Legal RAG Bench | `isaacus-dev/legal-rag-bench@9e30a36d` | 端到端法律检索与推理、来源和无关上下文 | 外部公开冻结 |
| Agent Security Bench | `agiresearch/ASB@1f561dcc` | memory poisoning 与 observation injection | 外部公开冻结 |
| Independent Knowledge OS | 第三方在交付候选前提交数据承诺 | 来源、更新、遗忘、冲突、污染、预算和任务成功的综合盲测 | 外部秘密 held-out |
| Independent CN Legal | 第三方法律评测方在交付候选前提交数据承诺 | 中文法源版本、时效、近似条款、无答案、引用和越权变更 | 外部秘密 held-out |

协议共要求 10 个套件、55 个预注册具名基线和 11 个不可相互抵消的质量/安全/效率维度。
公开套件的仓库 commit 固定；数据集完整字节、官方 revision 和 SHA-256 在执行 manifest 中
再次绑定。两个隐藏套件必须在候选交付前提交 commitment。

当前提供两类可执行 DeepLaw 适配器：

- [`longmemeval_v2_deeplaw.py`](../benchmarks/external/adapters/longmemeval_v2_deeplaw.py)
- [`jsonl_corpus_deeplaw.py`](../benchmarks/external/adapters/jsonl_corpus_deeplaw.py)
- [`适配说明`](../benchmarks/external/adapters/README.md)

前者是 LongMemEval-V2 官方接口的 text operating point，不会把忽略 query image 的结果
伪装成多模态结果；后者把评测方的封闭 `{id,title,text}` 语料和 `{case_id,query}` 问题通过
真实 Knowledge Compiler、人工 fixture 审核边界和 Context Compiler，保留原始评测 ID，
便于接入法律片段与秘密检索套件。它不是通用“自动适配所有 benchmark”的宣称。

## 相同条件

每个套件都必须固定并保留：

1. 完整问题集合和语料 SHA-256；
2. 同一 reader 模型、版本、推理参数和 judge；
3. 同一上下文 Token 上限，而不只是相同 top-k；
4. 相同查询文本，不为 DeepLaw 单独重写问题；
5. 索引构建时间、模型调用、磁盘、内存、查询延迟和成本；
6. 所有失败、拒答、无答案和超时样本；
7. candidate wheel/container、依赖锁、Git commit 和运行日志；
8. 基线的固定上游 commit、配置和原始输出。

协议列出的 baseline 不是候选池。每个套件必须提交全部预注册
`named_baselines × required_dimensions` 比较；不能在看到结果后只保留 DeepLaw 获胜的基线或
指标。候选、基线逐题报告和 comparison 都由 hash 绑定。

候选在协议冻结后不得根据测试结果修改阈值、提示词、模型、切分或查询路由。任何修改都形成
新版本和新协议，旧结果不能继续挂到新版本上。

## 不允许被平均分隐藏的十一个维度

协议要求同时覆盖：

- `task_success`：Agent 最终任务是否完成；
- `useful_context_recall`：必要证据是否被选入；
- `irrelevant_context_rate`：送入模型的无关内容比例；
- `provenance_coverage`：返回内容是否可回到固定来源；
- `stale_contradiction_detection`：过期和冲突是否被识别；
- `forgetting_accuracy`：明确退出、撤销或过期的知识是否不再影响任务；
- `memory_poisoning_success_rate`：攻击内容是否成功影响后续 Agent；
- `unauthorized_mutation_success_rate`：Agent 是否能越过只读边界修改知识；
- `context_chars`：完整上下文负担；
- `query_latency_ms`：查询延迟。
- `amortized_total_cost_usd`：索引、模型与查询成本按相同注册查询量摊销。

主指标使用配对 bootstrap 10,000 次和 95% 置信区间。任务成功与必要上下文召回预注册至少
`1%` 的绝对优势，并对全部优势假设实际执行 Holm–Bonferroni family-wise 校正。安全、来源、
噪声、上下文和延迟采用严格零容忍非劣边界，不能用任务平均分抵消。

## 真正 held-out 的交付方式

独立评测方需要在收到 DeepLaw 候选前完成：

1. 生成或选定未公开的测试实例和标签；
2. 公布题目数量、能力分层、生成规则、预算和数据承诺 hash，但不公开内容；
3. 保存只读时间戳、数据 SHA-256 和评测代码 commit；
4. 接收固定 wheel/container，只允许协议声明的模型端点，禁止候选联网回传；
5. 在同一硬件、模型和预算上运行 DeepLaw 与具名基线；
6. 输出逐题结果、资源计量、失败样本，生成封闭 suite evidence manifest，并用独立
   Ed25519 密钥签名绑定该 manifest 的 attestation；
7. 在成绩冻结前不向 DeepLaw 团队泄露题目、标签或中间排名；
8. 由第二家机构从固定产物复现至少一个公开套件和该 attestation。

开发团队可以在最终冻结后看到失败样本，但任何针对性修复只能进入下一版本。

## 可执行工具

```bash
# 精确 ID 检索评分
uv run python benchmarks/external/score_retrieval.py --help

# 把上游逐题官方分数规范化
uv run python benchmarks/external/normalize_metrics.py --help

# 同题配对 bootstrap
uv run python benchmarks/external/compare_reports.py --help

# 生成供独立评测方签名的封闭 suite evidence manifest
uv run python benchmarks/external/build_suite_manifest.py --help

# 宣称门禁；pending 证据按设计返回退出码 2
uv run python benchmarks/external/claim_gate.py \
  --protocol benchmarks/external/protocol-v2.json \
  --evidence benchmarks/external/claim-evidence.pending.json
```

所有输入都要求完整覆盖、唯一 case ID、有限数值、固定 hash 和原始输出。独立评测者必须提交
可验证的 Ed25519 签名，DeepLaw 维护者自身不能计入独立评测者。门禁不接受只有一行平均分的
截图或维护者自行填写的“已领先”布尔值。

每个 run 的 suite evidence manifest 同时绑定：

- protocol、候选 commit 和 wheel/container SHA；
- 上游 repository/dataset revision 与完整数据 SHA；
- reader model/revision、Token 预算、硬件、开始/结束时间；
- 索引时间、峰值内存、磁盘和模型成本；
- 原始输出；
- 候选/每个基线的逐题报告；
- 每个 baseline × dimension 的比较文件。

comparison 必须绑定两个规范化报告的 canonical SHA，并由门禁重算逐题平均差；CI、p 值、方向、
样本数、seed、最小效应和非劣 margin 必须与冻结协议一致。独立评测方签的是完整 manifest，
因此不能把一家机构的原始输出、另一版本的比较文件和维护者自填的通过标记拼接成证据。
只有实际绑定至少一个当前 suite manifest 的评测机构才计入“独立评测者”数量。

协议本身的 canonical SHA-256 固化在
[`claim_gate.py`](../benchmarks/external/claim_gate.py)。复制协议后降低套件数、基线数、效应量
或签名要求会直接失败，而不是继续沿用同一个 protocol ID。

## 当前诚实状态

60 题 LongMemEval-S cleaned 开发诊断已经实际运行，结果见
[`longmemeval-s-dev-2026-07-26.json`](../benchmarks/external/longmemeval-s-dev-2026-07-26.json)。
它发现并推动修复了首个长资产独占预算、非查询相关截断、同源分段拥挤和英文问句脚手架噪声。
最终源码绑定重跑中，Capsule Recall@5 为 `0.8906`、Hit@1 为 `0.85`，重复归零，平均正文
为 `3,646.8` 字符；相对原始 search，Capsule 无关内容率从 `0.6292` 降到 `0.3342`。
批量 source 审核后，单 case 平均摄取从早期逐 Asset 完整性重放的约 `2.9 s` 降至
`0.139 s`。报告保留 60 题逐题 ID、返回、预算、延迟、隔离资产计数和实现文件 hash。

这组数据已经被开发团队查看并用于改进，因而机器标记为 `claim_eligible=false`。它只证明
诊断和修复过程，不证明 DeepLaw 对外领先。

剩余失败集中在偏好和语义改写：10 个 `single-session-preference` case 的 Capsule Hit@1
为 `0.20`、Recall@5 为 `0.60`、无关率为 `0.85`；其余 50 题 Hit@1 为 `0.98`。原始会话
reference 因而不能被表述为已提炼的长期偏好。正确下一步是把语义发现做成可删除、非权威
sidecar，在上述冻结套件中验证它是否提高最终任务成功，同时不损害来源、安全、上下文、延迟
和成本；在通过前不进入默认路径。

当前
[`claim-evidence.pending.json`](../benchmarks/external/claim-evidence.pending.json)
会被门禁明确阻断。只有外部完整运行、第三方秘密留出和独立复现到齐后，状态才能改变。

## 外部评测方交付检查表

1. 在接收候选前提交隐藏数据 commitment；
2. 记录候选 commit、artifact SHA、上游 revision 和完整数据 SHA；
3. 保留全部逐题原始输出、失败、拒答和超时；
4. 使用仓库 scorer/normalizer 生成候选与全部基线报告；
5. 使用冻结参数生成全部 comparison；
6. 生成 suite evidence manifest，并核对其中每个 artifact SHA；
7. 以机构 Ed25519 密钥签署只包含封闭字段的 attestation；
8. 由第二家机构独立覆盖至少一个真实 run；签署未出现的 suite 不计数；
9. 运行 claim gate 并归档 stdout、退出码、环境和 artifact；
10. 只有 gate 返回的 `allowed_claim` 可以进入发布说明。

## 上游依据

- [LongMemEval-V2 official repository](https://github.com/xiaowu0162/LongMemEval-V2)
- [MemoryAgentBench official repository](https://github.com/HUST-AI-HYZ/MemoryAgentBench)
- [Memora official repository](https://github.com/geniesinc/Memora)
- [STATE-Bench official repository](https://github.com/microsoft/STATE-Bench)
- [Agent Memory Benchmark official repository](https://github.com/vectorize-io/agent-memory-benchmark)
- [LegalBench-RAG official repository](https://github.com/zeroentropy-ai/legalbenchrag)
- [Legal RAG Bench official repository](https://github.com/isaacus-dev/legal-rag-bench)
- [Agent Security Bench official repository](https://github.com/agiresearch/ASB)
