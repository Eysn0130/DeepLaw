# DeepLaw 评测说明

## 当前结论

当前记录是 2026-07-15 对一个本地 candidate 数据库运行的历史白盒 smoke snapshot。它只能
证明所记录的实现、case 文件和数据库组合在该次运行中通过已编码断言，不能证明当前或
未来任意 release 自动通过，也不能证明 DeepLaw 已经超过 gbrain、RAGFlow、PageIndex、
KAG、GraphRAG、LLM Wiki 或任何其他系统。`evals/core-2026-07-14.jsonl` 是按已知 28 份
语料设计的 32 项白盒 smoke set，不是盲测、留出集或独立专家金标。

2026-07-15 候选结果记录在
[`benchmarks/core-candidate-2026-07-15.json`](../benchmarks/core-candidate-2026-07-15.json)：

- 32/32 同时通过检索目标与噪声/上下文约束；
- 30 个有排名目标的 case 为 Hit@1 1.0、MRR 1.0；
- 平均 evidence excerpt 666.344 字符；
- 本机 p50 11.125 ms、p95 17.049 ms；
- 精确“法名 + 条号”查询只返回目标条款；
- 不存在的精确条号和一个尚未施行文件的时点负例都断言为空结果；
- 基础条例和同名实施细则冲突时，精确题名只返回目标文档；
- `诈骗` 单词导航断言 navigation mode、最多 3 张证据卡和最多 1,200 excerpt 字符；
- OCR case 断言首张证据卡保留 `extraction_review_required: true`。

这些指标绑定 JSON 中确切的 `release_id`、数据库 hash、来源 manifest hash、case 文件 hash、
完整 Python source-tree hash、四个关键实现文件 hash 和本机环境。其状态是
`candidate_smoke_not_held_out`；语料数据库
本身因许可尚未评估而不进入 Git，时效和再分发复核也仍未完成。因此该 JSON 不是已批准
release 的发布证明，也不能单独证明该数据库已按当前 extraction/provenance schema 重建。
OCR 断言只覆盖“人工复核标记被返回”，不验证逐页文字、坐标或置信度准确性。

本次同步时的 focused test suite 另有 53 项测试，覆盖未知或扩展法名配合形式合法的条号时
失败关闭、基础法与修正案/实施细则的精确身份优先级、release/SQLite 边界、OCR 页序和字符
预算。该测试计数不是 benchmark JSON 中的 case 数，也不改变上述 candidate 和白盒边界。

外部复现需要调用者合法取得确切 candidate 数据库，或取得同一 source package、匹配的
构建实现及外部提取工具后重新构建。重新构建产生不同 release 时，应作为新快照评测，不能
把本文件中的历史 ID 或数字沿用为结果。

## 运行方法

```bash
DEEPLAW_DB="${DEEPLAW_DB:?set DEEPLAW_DB to the candidate database}"

deeplaw doctor --db "$DEEPLAW_DB"
deeplaw eval \
  --db "$DEEPLAW_DB" \
  --cases evals/core-2026-07-14.jsonl \
  --limit 5 \
  --output tmp/core-eval-report.json
```

评测器检查：

- expected title/article 是否出现及其 rank；
- `expected_empty` case 是否确实没有返回证据；
- forbidden title 是否被错误返回；
- expected route；
- evidence 数量和 excerpt 字符预算（不是完整序列化响应的字节预算）；
- 指定 case 的 `extraction_review_required` 标记；
- retrieval、constraint 和 overall pass rate；
- Hit@1、MRR、p50/p95 latency；
- release、database、source manifest 和 case hash。

`evals/activation-boundary.jsonl` 是宿主激活正负例。DeepLaw 本身无法证明 Codex、Claude
Code、OpenCode 或未来 Analytix 的模型一定遵守 Skill；必须在每个宿主测试“未安装/已安装但
未激活/显式激活”三种状态的 provider-visible schema、路由、Token 和工具调用。

## 下一阶段硬门禁

生产和对外性能主张需要独立专家标注的 `DeepLawBench-CN`，至少增加：

- 留出法源与盲测问题；
- 公布、未施行、部分修订、废止、替代和历史条文链；
- 文号、别名、近似条号和错误版本干扰；
- 字符区间与页码/坐标 precision/recall；
- 无答案、库外问题和相似条款误召回；
- 去标识化多规则事实问题；
- 非法律任务误激活；
- OCR 逐页人工金标；
- 相同数据集上的 BM25、dense、gbrain-style hybrid/RRF、PageIndex、图谱增强和完整
  DeepLaw 梯子基线；
- latency、内存、磁盘、模型调用成本、置信区间和失败样本。

错误版本率、来源/hash 覆盖率和引用区间错误率是硬门禁，不能用平均召回率抵消。在完成这组
对照前，只能表述为“该历史 candidate smoke snapshot 覆盖了已编码的版本、证据和 excerpt
预算回归”；不能表述为这些风险已经受控或生产 release 已就绪。
