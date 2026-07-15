<h1 align="center">DeepLaw</h1>

<p align="center">
  <img src="https://raw.githubusercontent.com/Eysn0130/DeepLaw/main/assets/brand/deeplaw-lockup.svg" width="560" alt="DeepLaw — source-bound legal evidence for agents" />
</p>

<p align="center">
  <strong>把法律检索从“相似文本注入”升级为可执行、可拒答、可校验的证据协议。</strong><br />
  Version-aware legal evidence infrastructure for Codex, Claude Code and OpenCode; designed for future Analytix integration.
</p>

<p align="center">
  <a href="https://github.com/Eysn0130/DeepLaw/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Eysn0130/DeepLaw/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <img src="https://img.shields.io/badge/status-alpha-F2A65A?style=flat-square" alt="Alpha" />
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11 and 3.13" />
  <img src="https://img.shields.io/badge/MCP-read--only-6F5CF1?style=flat-square" alt="Read-only MCP" />
  <img src="https://img.shields.io/badge/context-%E2%89%A45%20cards%20%2F%206000%20chars-0F766E?style=flat-square" alt="At most five cards and 6000 characters" />
  <a href="https://github.com/Eysn0130/DeepLaw/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-2D3748?style=flat-square" alt="Apache-2.0" /></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Eysn0130/DeepLaw/main/assets/readme/evidence-execution-badge.svg" width="250" height="55" alt="DeepLaw Evidence Execution Engine — source, version, proof and explicit gaps" />
</p>

---

DeepLaw 是一个独立、只读、内容寻址的中国法律证据底座。它不把向量相似度、图关系或
LLM 生成页面当作法律真相；它先把问题编译成有限的证据义务，再在固定法源版本中执行检索，
最后只向 Agent 交付一个有界、可验证且能明确暴露缺口的 proof packet。

这不是“换一种 RAG”。DeepLaw 的核心抽象是 **Evidence Execution Engine**：RAG、
GraphRAG、树检索、reranker 和 Wiki 都可以成为未来的候选生成器，但永远不能绕过版本门、
权威层、上下文预算、缺口判断和 receipt 校验。

<p align="center">
  <img src="https://raw.githubusercontent.com/Eysn0130/DeepLaw/main/assets/readme/architecture.svg" width="1120" alt="DeepLaw architecture: source, version gate, query plan, evidence graph, bounded proof pack, immutable receipt and agent" />
</p>

## 为什么需要 DeepLaw

传统知识库通常优化“找到更多相似内容”，而法律研判首先需要回答另外几件事：这是哪一个
文件、哪个版本、在什么时间范围内、为什么命中、有哪些相反规则、还有什么没有被证明。

| 常见方案 | 典型短板 | DeepLaw 的约束 |
| --- | --- | --- |
| 向量 RAG | 泛词产生大量近似片段；高 recall 直接变成上下文噪声 | 精确检索和中文 FTS 优先；语义通道只能有界兜底 |
| GraphRAG | 图边可能由模型推断；社区摘要容易被误当事实 | 图只做导航；每条边绑定来源 segment、hash 和 review status |
| LLM Wiki | 结构好读，但派生叙述会老化、合并版本或丢失例外 | Wiki 只能重建和删除，不能覆盖原文或成为最终引证 |
| 长上下文全文注入 | 成本、注意力稀释和 prompt cache 退化 | `search` 最多 5 卡/6000 字，全文必须按 `segment_id` 二次读取 |
| 关键词规则 | 命中可解释，但不知道任务到底缺什么 | QueryPlan 把请求编译成 primary rule、时效、要件、反证等义务 |

DeepLaw 的创新点不是“同时使用更多检索器”，而是把检索结果变成一个可审计执行结果：

```text
Question
  -> closed QueryPlan
  -> exact / article / lexical candidates
  -> temporal trust buckets
  -> provenance-carrying legal graph
  -> obligation coverage + explicit gaps
  -> bounded proof packet
  -> source/segment/release receipt
```

如果义务没有覆盖，DeepLaw 返回 `gaps`；如果时效元数据没有通过 release 级核验，候选进入
`uncertain_evidence`；如果已知效力区间不含目标日期，候选不会进入主证据。它不会为了给出
“看起来完整”的答案而回退到模型记忆或未核验网络搜索。

## 已实现的能力

### 1. Evidence execution

- 确定性 `QueryPlan`：8 类封闭证据义务、稳定 plan ID 和硬边界；
- 三种路由：精确法条、主题导航、研究问题；
- `obligation_coverage`：区分 `covered`、`uncertain` 和 `gap`；
- 结构化 `gaps`：精确目标未解析、时效未核验、区间外、主证据缺失等；
- 证据卡总量、excerpt 字符数、图路径和 hop 数均有硬上限。

### 2. Authority and time

- 原件路径、格式、大小和 SHA-256 构建前校验；
- 法名、文号、别名、条款、发布/施行/失效时间和状态；
- `as_of` 使用 `verified_in_scope` / `unverified_metadata` /
  `outside_effective_interval` 三分法，未知时间不再混入主证据；
- hash 绑定的 review overlay 可以收窄错误状态，但 AI-only overlay 无权把 release 标为
  `verified` 或批准再分发；
- 从来源 segment 精确识别的废止、修订、替代、实施和引用关系进入带 provenance 的
  `legal_edges`；overlay 中的关系只作为 hash 绑定的治理提案，不会直接进入运行时图。

### 3. DeepLaw Vision

- DOCX 直接解析 OOXML，保留段落、表格行和脚注引用；
- 文本 PDF 优先读取原生文本层；
- 每页记录 image/native/OCR/selected text hash、字符数、一致度、OCR 置信度和风险标志；
- 原生层异常时才启动本地 OCR；低置信度或原生/OCR 分歧保持 `review_required`；
- 人工校对文件必须同时绑定源 PDF SHA 和渲染页 SHA，并声明人工身份、时区时间和视觉比对
  attestation；管线自身不能生成或冒充 `human_reviewed`。

### 4. Immutable releases

- release ID 绑定来源 metadata、提取版本/配置、页级证据、segment hash、关系图和 SQLite
  schema；
- 同 ID 不覆盖，构建目录原子发布；
- SQLite 使用 `mode=ro&immutable=1`，运行时无语料写接口；
- `receipt_id` 绑定 release/document/segment/source/text hash；
- `verify` 会重算 segment hash 并验证 receipt，不把成功表述成“重新核验了官方网站”。

### 5. One small Agent surface

DeepLaw 只暴露一个 MCP leaf tool：`law_support`。内部只有四个只读操作：

| Operation | 作用 |
| --- | --- |
| `search` | 返回有界主证据、不确定证据、图路径、覆盖状态和缺口 |
| `get` | 按精确 `segment_id` 读取被选中的规范文本 |
| `verify` | 验证 receipt 和当前不可变 release 中的 segment hash |
| `release_info` | 检查固定 release、schema、审核与再分发状态 |

普通数据分析、SQL、代码或文档任务不应经过 DeepLaw。安装插件不等于每轮自动调用；当前
插件把只读工具注册给宿主，是否能在 provider tool schema 物化前完全隐藏它取决于宿主。
未来 Analytix 接入必须把显式法律意图门禁放在 schema 物化之前，并用 inactive A/B 测试证明
普通任务零影响。

## 返回结果长什么样

下面是一个可由当前运行时生成的“零命中”响应形态，不包含任何法源正文；ID 使用示意值：

```json
{
  "schema_version": "deeplaw.search-response/v2",
  "release_id": "lawrel_11111111111111111111111111111111",
  "mode": "research",
  "query_plan": {
    "schema_version": "deeplaw.query-plan/v1",
    "plan_id": "lawplan_641f0c887156e56449cf71cb2453d87b",
    "query": "某规范文件是否现行",
    "purpose": "auto",
    "route": "research",
    "as_of": null,
    "obligations": [
      {
        "id": "primary_rule",
        "role": "support",
        "required": true,
        "query_cues": ["purpose:auto", "route:research"]
      },
      {
        "id": "temporal_status_version",
        "role": "temporal",
        "required": true,
        "query_cues": ["text:现行"]
      },
      {
        "id": "exceptions_counterevidence",
        "role": "counterevidence",
        "required": true,
        "query_cues": ["route:research"]
      }
    ],
    "bounds": {
      "max_query_chars": 8000,
      "max_obligations": 8,
      "max_query_cues_per_obligation": 8
    },
    "channels": ["exact_metadata", "article_locator", "chinese_fts"],
    "document_types": [],
    "max_evidence": 5,
    "max_chars": 3500,
    "max_graph_paths": 4,
    "max_hops": 1,
    "graph_used": false,
    "temporal_reference_date": null,
    "temporal_reference_source": "release_review_unavailable",
    "vector_used": false,
    "wiki_used": false
  },
  "evidence": [],
  "uncertain_evidence": [],
  "graph_paths": [],
  "obligation_coverage": [
    {
      "obligation_id": "primary_rule",
      "role": "support",
      "required": true,
      "status": "gap",
      "evidence_segment_ids": [],
      "graph_path_ids": []
    },
    {
      "obligation_id": "temporal_status_version",
      "role": "temporal",
      "required": true,
      "status": "gap",
      "evidence_segment_ids": [],
      "graph_path_ids": []
    },
    {
      "obligation_id": "exceptions_counterevidence",
      "role": "counterevidence",
      "required": true,
      "status": "gap",
      "evidence_segment_ids": [],
      "graph_path_ids": []
    }
  ],
  "gaps": [
    {
      "code": "required_obligation_uncovered",
      "obligation_id": "primary_rule",
      "message": "当前有界检索未覆盖该必需检索义务。",
      "blocking": true,
      "candidate_count": 0
    },
    {
      "code": "required_obligation_uncovered",
      "obligation_id": "temporal_status_version",
      "message": "当前有界检索未覆盖该必需检索义务。",
      "blocking": true,
      "candidate_count": 0
    },
    {
      "code": "required_obligation_uncovered",
      "obligation_id": "exceptions_counterevidence",
      "message": "当前有界检索未覆盖该必需检索义务。",
      "blocking": true,
      "candidate_count": 0
    },
    {
      "code": "no_primary_evidence",
      "obligation_id": null,
      "message": "当前有界检索未形成可进入主证据桶的候选。",
      "blocking": true,
      "candidate_count": 0
    }
  ],
  "notices": [
    "检索结果是研究证据候选，不等同于本案法律适用结论。",
    "DeepLaw 未使用模型记忆、自动 Web 回退或向量 top-k 注入。",
    "当前 release 未找到足够证据；这不表示相关法律不存在。",
    "当前 release 缺少 reviewed_on，无法把未指定 as_of 的问法解释为已复核的现行状态。"
  ],
  "next_questions": [],
  "total_excerpt_chars": 0
}
```

空的 `evidence` 不是失败，也不表示相关法律不存在；它表示当前固定 release 无法在既定信任
边界内完成该义务。

## 快速开始

需要 Python 3.11+ 和 [`uv`](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/Eysn0130/DeepLaw.git
cd DeepLaw
uv sync --extra dev
uv run deeplaw --version
```

使用操作者自己合法取得并保留的 source package：

```bash
export DEEPLAW_SOURCE_ROOT="/path/to/legal-source-package"
export DEEPLAW_SOURCE_MANIFEST="$DEEPLAW_SOURCE_ROOT/manifest.json"

uv run deeplaw build \
  --source-root "$DEEPLAW_SOURCE_ROOT" \
  --manifest "$DEEPLAW_SOURCE_MANIFEST" \
  --pdf-fallback vision-consensus \
  --allow-needs-ocr \
  --output-root "$HOME/.deeplaw/releases" \
  --activate
```

`--allow-needs-ocr` 只允许生成明确标记为不完整的 candidate release，不会把低质量页面升级
为已审核。生产发布必须使用源 SHA 命名的人工页审文件，并通过独立的法源、版本、隐私和许可
闸门。仓库内的 `governance/core-2026-07-14.ai-review.json` 只适用于其绑定的 28 个来源
hash；其他 source package 不得复用该 overlay。匹配该 manifest 时可显式追加
`--review-overlay governance/core-2026-07-14.ai-review.json`，不匹配会失败关闭。

```bash
uv run deeplaw doctor
uv run deeplaw search --query "刑法第二百六十六条" --as-of 2024-07-01
uv run deeplaw get --segment-id "seg_..."
uv run deeplaw verify --segment-id "seg_..." --receipt-id "lawrcpt_..."
uv run deeplaw mcp --stdio
```

单独审查 PDF 的页级证据：

```bash
uv run deeplaw pdf-evidence --source "/path/to/document.pdf"
```

## Agent 接入

- Codex / Claude Code 插件：[`plugins/deeplaw`](plugins/deeplaw)
- OpenCode 配置：[`adapters/opencode`](adapters/opencode)
- 适配器说明：[`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md)
- Analytix 下一步接入设计：[`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md)

当前没有修改 Analytix。未来接入必须保证 inactive 时普通任务的 route、provider-visible tool
schema、stable prefix、request body 和 token 数与未安装 DeepLaw 的基线等价；法律服务损坏或
离线也不能拖垮 DuckDB、SQLite、代码和文档能力。

## 核心包审计状态

仓库提供的是代码和 metadata review overlay，不分发 28 份 DOCX/PDF、完整规范文本、案例
材料或生成的 SQLite release。当前 overlay 已逐项完成 AI precheck，并在适用且有依据时补充
文号、发布机关、日期、状态风险和关系提案；它并不声称每一字段都已补齐。其 `reviewerKind`
是 `ai_precheck`，release 只能是 `partially_verified` 且
`redistributionStatus=restricted`。

四个已被硬门禁阻断的高风险项：

- 2020 刑法整合文本在 2024-03-01 后不能脱离修正案（十二）单独作为当前全文；
- 已于 2021-05-01 废止的行政法规只能进入历史证据桶；
- 2021 反洗钱监管办法在 2025-12-01 后必须与修改决定组合；
- 原包误标“现行整合文本”的央行规章实际缺少 2018/2025 修改，已重命名并限制为历史候选。

完整边界见 [`docs/SOURCE_AUDIT_2026-07-14.md`](docs/SOURCE_AUDIT_2026-07-14.md) 和
[`governance/core-2026-07-14.ai-review.json`](governance/core-2026-07-14.ai-review.json)。

## 质量、评测与诚实边界

```bash
uv lock --check
uv run ruff check .
uv run pytest
uv run deeplaw eval --cases evals/core-2026-07-14.jsonl --limit 5
git diff --check
```

现有 smoke benchmark 用于验证固定核心包上的命中、上下文预算和 receipt 链路，不是跨系统
冠军榜。当前 v0.2 本地候选在 32 项已知语料白盒 smoke case 上为 32/32；结果、hash 和限制见
[`benchmarks/core-v2-candidate-2026-07-15.json`](benchmarks/core-v2-candidate-2026-07-15.json)。
DeepLaw 只有在公开 held-out 中文法律集上同时报告版本正确率、引用 span、义务覆盖、
反证召回、上下文字符、延迟和成本后，才会发布可比较的领先性结论。

换句话说：架构目标可以领先，市场宣传必须等证据。

## 项目边界

DeepLaw 不做以下事情：

- 不预测有罪、量刑、责任或案件结果；
- 不把日期命中等同于本案适用；
- 不把案件私有文档、聊天、身份或交易数据写入公共 release；
- 不让 LLM 决定修订、废止或覆盖法律原文；
- 不在 MCP 运行时采集、构建、激活或修改语料；
- 不因一个数据字段叫“诈骗”就把普通数据分析带入法律工作流。

DeepLaw 输出的是法律研究证据候选，不是法律意见、事实认定或裁判结论。

## 文档地图

- 架构与信任模型：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- 语料治理：[`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md)
- Benchmark：[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)
- 上游研究与复用边界：[`docs/UPSTREAM_REUSE.md`](docs/UPSTREAM_REUSE.md)
- 安全策略：[`SECURITY.md`](SECURITY.md)
- 贡献指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)

## Roadmap

- [x] 不可变 SQLite release、receipt 和只读 MCP
- [x] QueryPlan、义务覆盖、显式 gaps 和严格时间分桶
- [x] provenance graph 与单跳有界导航
- [x] DeepLaw Vision 页级证据和人工审校 attestation
- [x] 28 项 hash-bound AI metadata precheck
- [ ] 双人法源/OCR/许可签字与签名 release
- [ ] 受控更新、撤销、supersession feed 和差分验证
- [ ] held-out 中文法律检索/版本/反证公开 benchmark
- [ ] 通过净增益门禁后的可选语义候选通道
- [ ] Analytix turn-scoped 激活与 inactive zero-impact A/B gate

## Community and license

欢迎使用 synthetic fixture 提交可复现的检索、版本、解析和安全问题。请不要在 issue、PR、
日志或截图中发布案件私有材料、完整法源正文、凭证或生成 release。参见
[`CONTRIBUTING.md`](CONTRIBUTING.md)、[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) 和
[`SECURITY.md`](SECURITY.md)。

DeepLaw 源代码按 [Apache License 2.0](LICENSE) 发布。该许可不自动授予外部法源、案例、
网站版式、第三方商标、模型或工具的再分发权。品牌资产为 DeepLaw 原创；名称仍建议在商业
发布前完成目标司法辖区的专业商标检索。
