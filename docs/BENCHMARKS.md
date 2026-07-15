# DeepLaw 2.0 评测说明

## 当前 v0.3.0 / SQLite v5 候选结果

当前可复现摘要记录在
[`benchmarks/core-v5-candidate-2026-07-15.json`](../benchmarks/core-v5-candidate-2026-07-15.json)，
绑定 `deeplaw.release/v2` / `deeplaw.sqlite/v5`、最终 28 份本地 release、签名目录、review
overlay、37 项 case 文件、当前 Python source tree 与关键实现 hash：

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

`core-v3-candidate-2026-07-15.json`、`core-v2-candidate-2026-07-15.json` 和
`core-candidate-2026-07-15.json` 是 v4/v3 历史快照，不代表当前实现。外部复现需要调用者合法
取得相同原件，或用相同 source manifest、overlay 和匹配实现重建；不同 release 必须产生新的
评测快照，不能沿用这里的数字。

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
- 相同数据集上的纯词法、纯语义、混合定位、结构树、关系增强和完整 DeepLaw 梯子基线；
- latency、内存、磁盘、模型调用成本、置信区间和失败样本。

错误版本率、来源/hash 覆盖率和引用区间错误率是硬门禁，不能用平均召回率抵消。在完成这组
对照前，只能表述为“该 candidate smoke snapshot 覆盖了已编码的版本、证据和 excerpt
预算回归”；不能表述为这些风险已经受控或生产 release 已就绪。
