# DeepLaw 2.0 评测说明

## 当前 v0.4.0 安装后快照

当前可复现摘要记录在
[`benchmarks/core-v0.4.0-candidate-2026-07-26.json`](../benchmarks/core-v0.4.0-candidate-2026-07-26.json)，
绑定安装后的 `deeplaw 0.4.0`、签名目录 sequence 2、28 份本地 release、当前 Python source
tree、固定文档模型文件清单、review governance/build-report 身份和更新后的时效分桶契约。

当前黑盒重建固定：

- release `lawrel_5fd93dee88cd55c68706cd1a3dfa527a`；
- database SHA-256
  `c5582f5f1553cbcaa4df4a263a16be524307051b7bfa6ded50053dbe3b4287db`；
- 28 份文档、3237 个 segment、111 条确定性关系；
- 15 个风险页精确传播到 8 个 segment。

当前用例固定在
[`evals/core-v0.4.0-2026-07-25.jsonl`](../evals/core-v0.4.0-2026-07-25.jsonl)。
历史 `evals/core-2026-07-14.jsonl` 保持原始 hash，不用新规则原地改写，因而 v0.3.0
快照仍可按其记录复现。

这次复核先暴露并修正了一个评测 P1：实现已经按安全规则把已知历史、废止、替代或尚未施行
法源在缺少 `as_of` 时移入 `uncertain_evidence`，旧用例却仍要求它们进入主证据。最初运行因此
只有 28/37 通过；9 个失败用例实际都准确返回了不确定候选且 receipt 有效。用例没有放宽标题、
条款、禁入噪声或预算要求，只把预期桶改为与当前权威边界一致。

修正后的当前快照：

- 37/37 通过已编码的检索、分桶、禁入噪声、预算和 receipt 约束；
- 27 个用例以主证据为目标，10 个用例明确以 `uncertain_evidence` 为目标；
- 34 个有排名目标的用例为 Hit@1 0.971、MRR 0.985；
- 37 张返回卡 receipt 往返核验通过率为 1.0；
- 平均 evidence excerpt 为 230.541 字符，平均完整 search response 为 5985.676 字符；
- 已打开数据库后的 `law.search()` 为 p50 16.502 ms、p95 28.871 ms；
- 25/37 个用例保留 83 个 blocking gap；138 个必需 Duty 中 68 个 covered、26 个 uncertain、
  44 个 uncovered，covered rate 为 0.493。

最后一组数字和 37/37 同等重要：当前结果证明系统能把预期的不确定来源隔离到正确桶，并不
表示这些查询已经具备可直接引用的主证据，更不表示法律适用结论完整。release 仍为
`partially_verified`、`restricted`、`ai_precheck`。这不是盲测、独立专家金标或外部系统对照，
不能据此宣称跨系统领先。

2026-07-25 的
[`core-v0.4.0-candidate-2026-07-25.json`](../benchmarks/core-v0.4.0-candidate-2026-07-25.json)
作为历史候选保留，不原地改写。2026-07-26 复核补齐了 `release_id` 对 review governance 和
build-report 身份的绑定，因此当前逻辑 release ID 已改变；新旧快照都同时绑定各自的
`release_id + database_sha256 + source tree`，不能混用。

## 历史 v0.3.0 / SQLite v5 候选快照

当前可复现摘要记录在
[`benchmarks/core-v5-candidate-2026-07-15.json`](../benchmarks/core-v5-candidate-2026-07-15.json)，
绑定当时的 `deeplaw.release/v2` / `deeplaw.sqlite/v5`、28 份本地 release、签名目录、review
overlay、37 项 case 文件、`v0.3.0` Python source tree 与关键实现 hash：

- 37/37 同时通过已编码的检索目标与噪声、分桶、卡片、excerpt 和 receipt 约束；其中新增
  4 项相邻罪名与错误标准负例，要求主题无法确定时失败关闭；
- 题名与条款同时存在时，必须由**同一张卡片**命中，不再把两张不同卡片拼成成功；
- 34 个有排名目标的 case 为 Hit@1 0.971、MRR 0.985；
- 37/37 张返回卡 receipt 往返核验通过率为 1.0；
- 平均 evidence excerpt 为 235.730 字符；平均完整序列化 search response 为 6362.135 字符，
  证明 excerpt budget 不等于整个 Agent 上下文或 Token budget；
- 已打开数据库后的 `law.search()` 本机延迟为 p50 16.722 ms、p95 30.102 ms；数据库打开、
  receipt 核验、JSON 序列化和 MCP transport 不计入该延迟；
- 20/37 个 case 保留至少一个 blocking gap，共 51 个；138 个必需 compiler Duty 中，91 个
  `covered`、3 个 `uncertain`、44 个 `uncovered`，covered rate 为 0.659。

最后一组数字是报告的重要组成部分：37/37 只说明预先编码的定位与安全回归通过，不说明每个
问题已有完整法源、Evidence Duty 已满足或可给出确定性案件适用结论。Skill 必须在回答前检查
`duty_witnesses`、`obligation_coverage`、`uncertain_duty_ids` 与全部 blocking gaps。

release 仍为 `partially_verified`、`restricted`、`ai_precheck`；语料二进制、Markdown 导出和
SQLite 不进入 Git。该结果不是盲测、留出集、独立专家金标或外部系统对照，不能证明法律内容
获人工批准，也不能证明 DeepLaw 超过其他方法。

本节全部数字都是 `v0.3.0` 历史候选证据，不代表当前 `v0.4.0` 实现。
`core-v3-candidate-2026-07-15.json`、`core-v2-candidate-2026-07-15.json` 和
`core-candidate-2026-07-15.json` 是更早的 v4/v3 storage 快照。外部复现需要调用者合法取得
相同原件，或用相同 source manifest、overlay 和匹配实现重建；不同软件版本或 release 必须产生
新的评测快照，不能沿用这里的数字。

## 运行方法

```bash
DEEPLAW_DB="${DEEPLAW_DB:?set DEEPLAW_DB to the candidate database}"

deeplaw doctor --db "$DEEPLAW_DB"
deeplaw eval \
  --db "$DEEPLAW_DB" \
  --cases evals/core-v0.4.0-2026-07-25.jsonl \
  --limit 5 \
  --output tmp/core-eval-report.json
```

评测器检查：

- expected title/article 是否由明确分桶中的同一张卡片满足及其桶内 rank；
- `expected_empty` case 是否在两个分桶都没有返回候选；
- forbidden title/article 是否在任一分桶被错误返回；
- expected route；
- 两个分桶合计数量和 excerpt 字符预算（不是完整序列化响应的字节预算）；
- 指定 case 的 `extraction_review_required` 标记；
- 每张主证据和不确定证据的 receipt、release、source hash 与 segment hash 往返核验；
- 预期的 blocking gap `(code, obligation_id)` 原子配对、必需 compiler Duty 的
  covered/uncertain/uncovered 数量，
  以及完整序列化响应字符；
- retrieval、constraint 和 overall pass rate；
- Hit@1、MRR、p50/p95 latency；
- release、database、source manifest 和 case hash。

`evals/activation-boundary.jsonl` 是宿主激活正负例。DeepLaw 本身无法证明 Codex、Claude
Code、OpenCode 或未来 Analytix 的模型一定遵守 Skill；必须在每个宿主测试“未安装/已安装但
未激活/显式激活”三种状态的 provider-visible schema、路由、Token 和工具调用。

## 外部 held-out 与宣称硬门禁

跨系统性能主张按
[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md) 执行。协议已冻结六个互补
套件、全部预注册 baseline × dimension、逐题配对统计、资源计量、秘密 held-out 和两家独立
Ed25519 attestation。机器门禁位于
[`benchmarks/external/claim_gate.py`](../benchmarks/external/claim_gate.py)；当前 pending
evidence 按设计返回退出码 `2`。

中文法律秘密套件仍需要独立专家标注，至少增加：

- 留出法源与盲测问题；
- 公布、未施行、部分修订、废止、替代和历史条文链；
- 文号、别名、近似条号和错误版本干扰；
- 字符区间与页码/坐标 precision/recall；
- 无答案、库外问题和相似条款误召回；
- 去标识化多规则事实问题；
- 非法律任务误激活；
- OCR 逐页人工金标；
- 相同数据集上的纯词法、纯语义、混合定位、结构树、关系增强和完整 DeepLaw 梯子基线；
- latency、内存、磁盘、模型调用成本、置信区间和失败样本。

错误版本率、来源/hash 覆盖率和引用区间错误率是硬门禁，不能用平均召回率抵消。公开
LongMemEval-S 60 题诊断已经参与开发，只能作为
[`claim_eligible=false`](../benchmarks/external/longmemeval-s-dev-2026-07-26.json) 的调试证据。
在六套件、隐藏数据和独立复现齐备前，只能表述为“该 candidate smoke snapshot 覆盖了已编码
的版本、证据和预算回归”；不能表述为跨系统领先。
