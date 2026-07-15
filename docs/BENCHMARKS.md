# DeepLaw 评测说明

## 当前 0.2 候选结果

2026-07-15 使用当前 `deeplaw.release/v2` / `deeplaw.sqlite/v4` 本地候选运行 32 项白盒
smoke set，结果记录在
[`benchmarks/core-v2-candidate-2026-07-15.json`](../benchmarks/core-v2-candidate-2026-07-15.json)：

- 32/32 同时通过检索目标与噪声/上下文约束；
- 30 个有排名目标的 case 为 Hit@1 1.0、MRR 1.0；
- 平均 evidence excerpt 679.719 字符；
- 109/109 张返回证据的 receipt 往返核验通过率为 1.0；
- 已打开本地数据库后的 `law.search()` 本机延迟为 p50 12.337 ms、p95 18.322 ms；数据库
  打开、receipt 往返断言、JSON 序列化和 MCP transport 均不包含在该延迟中；
- 精确题名聚焦、法名 + 条号、未来时点负例、历史标题纠偏、OCR review flag 和泛词预算均被
  固定断言覆盖；
- 两个 expected title 随 hash-bound overlay 的安全标题纠偏同步更新，cases SHA-256 因此变为
  `f60d88ab4b90e421b965e16ea85040edfab9dedb5cb387a1f92e20dd4ecc8d50`。

报告绑定 release、database、source manifest、review overlay、case 文件、Python source tree、
关键实现文件和本机环境。语料二进制及 SQLite 不进入 Git；release 仍是
`partially_verified`、`restricted`、`ai_precheck`。成功只能证明这组已知语料白盒断言，不能
证明法律内容已获人工批准，也不能证明 DeepLaw 超过任何外部系统。

`benchmarks/core-candidate-2026-07-15.json` 保留的是 0.1 / SQLite v3 历史快照，不代表当前
实现。两份报告均为 `candidate_smoke_not_held_out`，不是盲测、留出集或独立专家金标。

外部复现需要调用者合法取得确切 candidate 数据库，或用同一 source package、overlay 和
匹配构建实现重新生成。不同 release 必须作为新快照评测，不能沿用这里的数字。

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
- 每张主证据和不确定证据的 receipt、release、source hash 与 segment hash 往返核验；
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
对照前，只能表述为“该 candidate smoke snapshot 覆盖了已编码的版本、证据和 excerpt
预算回归”；不能表述为这些风险已经受控或生产 release 已就绪。
