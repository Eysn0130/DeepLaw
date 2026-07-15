# DeepLaw

DeepLaw 是面向 Codex、Claude Code、OpenCode 和未来 Analytix 的只读中国法律证据底座。
它优先解决法律真源、版本、定位、引用完整性和 Agent 上下文噪声，而不是把向量 top-k 或
LLM 生成 Wiki 直接当成法律知识真相。

当前代码为 `0.1.0` alpha。28 份法源已成功构建成本地 candidate release，但其法律时效、
案例隐私和再分发许可尚未全部人工批准；因此原始 DOCX/PDF、完整 SQLite release 和 OCR
正文不进入 GitHub，也不能把本地 `--activate` 理解为生产发布。

## 为什么不是朴素 RAG、gbrain 或 LLM Wiki

DeepLaw 使用“受约束检索增强”，但 0.1.x 不启用向量库：

```text
官方来源 + 原件 hash
  -> 确定性条款/章节结构
  -> 法名/条号/时点精确检索
  -> 中文 FTS5 小候选集
  -> 最多 1–5 张证据卡
  -> 按 segment_id 读取
  -> receipt 校验
```

向量、图谱、reranker、PageIndex 或 LLM Wiki 以后只能作为按 release/segment hash 绑定、
可删除、可重建的派生层。它们必须先在中文法律盲测上证明净增益，且不能决定效力、修改原文
或成为最终引用。gbrain、MinerU、OpenContracts、QuantLaw、KAG、RAGFlow 等取舍见
[`docs/UPSTREAM_REUSE.md`](docs/UPSTREAM_REUSE.md)。

## 已实现

- 校验 manifest 路径、格式、大小、SHA-256、重复文件和 HTTPS 来源声明；
- 直接解析 DOCX OOXML，保留段落、表格行和脚注引用；
- 解析文本层 PDF，并检测低文本覆盖、替换字符及伪合格的逐汉字空格 OCR 层；
- 可选本机 Tesseract/Poppler 或外部 MinerU fallback；MinerU 仅接受本地模型源；
- 按法条/章节确定性切分，保留页码或段落定位和 segment hash；
- 从来源、解析器版本、派生文本 hash、构建配方和存储 schema 生成内容寻址 release；
- 原子发布、同 ID 不覆盖、SQLite `mode=ro&immutable=1`、数据库只读权限和 receipt 校验；
- 中文二/三元词 FTS、法名/条号/时点优先、导航去重和严格 evidence/字符预算；
- OCR/解析警告与时效复核标记随证据卡返回；
- 唯一 MCP leaf `law_support`，内部只有 `search/get/verify/release_info` 四个只读操作；
- Codex、Claude Code、OpenCode 的显式调用 Skill/适配器；
- source-free 测试 fixture、JSON Schema、53 项测试和 32 项 candidate smoke benchmark。

尚未实现且不能伪装已实现：完整全国法源、经过批准的修订/废止血缘、release 签名/撤销服务、
OCR 全页人工金标、生产许可结论、向量 fallback、派生 Wiki，以及 Analytix 宿主改造。

## 安装和构建

需要 Python 3.11+ 和 `uv`：

```bash
uv sync --extra dev
uv run deeplaw --version
```

使用调用者自己合法取得的 source package：

```bash
export DEEPLAW_SOURCE_ROOT="/path/to/legal-package"
export DEEPLAW_SOURCE_MANIFEST="$DEEPLAW_SOURCE_ROOT/00-说明与清单/download-manifest.json"
export DEEPLAW_HOME="${DEEPLAW_HOME:-$HOME/.deeplaw}"

uv run deeplaw build \
  --source-root "$DEEPLAW_SOURCE_ROOT" \
  --manifest "$DEEPLAW_SOURCE_MANIFEST" \
  --output-root "$DEEPLAW_HOME/releases" \
  --pdf-fallback tesseract \
  --activate
```

`--pdf-fallback mineru` 只会调用操作者已经安装并审核许可的本机 MinerU CLI，并要求
`MINERU_MODEL_SOURCE=local`。DeepLaw 不会主动调用云端，但不替代操作系统级网络隔离；需要
离线保证时仍应在断网容器或受控主机中执行。DOCX 和质量合格的 PDF 不会被强制送入 MinerU。

```bash
uv run deeplaw doctor
uv run deeplaw search --query "刑法第二百六十六条 诈骗罪" --limit 3
uv run deeplaw get --segment-id "seg_..."
uv run deeplaw verify --segment-id "seg_..." --receipt-id "lawrcpt_..."
uv run deeplaw mcp --stdio
```

运行时默认读取 `~/.deeplaw/ACTIVE`；也可以用 `DEEPLAW_DB` 固定某个数据库，或用
`DEEPLAW_HOME` 指向包含 `ACTIVE` 与 `releases/` 的目录。这样 wheel、Codex、Claude Code
和 OpenCode 共用同一用户级法律库，不依赖当前工作目录。正式 Agent turn 会在 MCP 启动时
固定 release，不能依赖运行中变化的 `ACTIVE`。

## Agent 接入

共用插件位于 [`plugins/deeplaw`](plugins/deeplaw)，OpenCode 示例位于
[`adapters/opencode`](adapters/opencode)。安装方式、显式调用门禁和真实限制见
[`docs/AGENT_ADAPTERS.md`](docs/AGENT_ADAPTERS.md)。

Codex/Claude 启用插件后，宿主可能仍注册唯一的 MCP schema；Skill 禁止隐式调用不等于硬
工具权限隔离。要求非法律 turn 零 schema 影响时，应禁用插件/使用隔离 profile。未来
Analytix 必须在 provider tool schema 物化前按 turn 激活，设计见
[`docs/ANALYTIX_INTEGRATION.md`](docs/ANALYTIX_INTEGRATION.md)。本次没有修改 Analytix。

## 质量与治理

```bash
uv run ruff check .
uv run pytest
uv run deeplaw eval --cases evals/core-2026-07-14.jsonl --limit 5
```

当前 candidate smoke set 为 32/32 overall pass、30 个排名 case Hit@1/MRR 均为 1.0，
平均 excerpt 666.344 字符；其中含“诈骗”单词导航的 3 卡/1200 字符硬预算回归。这是白盒
结果，不是“超过所有 RAG/LLM Wiki”的证据。
完整结果和限制见 [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)。

- 架构：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- 语料治理：[`docs/CORPUS_GOVERNANCE.md`](docs/CORPUS_GOVERNANCE.md)
- 本次法源审计：[`docs/SOURCE_AUDIT_2026-07-14.md`](docs/SOURCE_AUDIT_2026-07-14.md)
- 上游与许可边界：[`docs/UPSTREAM_REUSE.md`](docs/UPSTREAM_REUSE.md)、
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)

DeepLaw 输出的是法律研究证据候选，不是法律意见、事实认定、罪名结论或案件裁判预测。
