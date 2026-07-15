# DeepLaw 2.0 Document IR 与文件摄取

Status: implemented baseline in `v0.3.0`, 2026-07-16.

## 结论

DeepLaw 2.0 不把 DOCX、PDF、TXT 或历史 DOC 统一“转换成 Markdown 后存库”。规范链路是：

```text
不可变原件 + SHA-256
  -> 有定位与质量证据的 Document IR
  -> 不可变 SQLite Knowledge Release
  -> 可删除、可重建的 Markdown / HTML / 搜索视图
```

Markdown 适合阅读，不适合承担以下规范信息：页面坐标、表格单元格、候选抽取、解析器配置、
置信度、逐页风险、版本事件、原件 hash 和人工复核绑定。因此 Markdown 只能是派生视图，不能是
法源真值或运行时数据库。

## 当前输入策略

| 输入 | 规范处理 | 原件 | 当前状态 |
| --- | --- | --- | --- |
| DOCX | 直接读取 OOXML，保留段落、样式、表格行和脚注引用 | 原字节保留并绑定 hash | 已实现 |
| PDF（可靠文本层） | native-first，按页保留文本与定位 | 原 PDF 保留并绑定 hash | 已实现 |
| PDF（扫描或风险页） | 页面图像、原文本层、独立 OCR 与结构化文档引擎候选进行逐页门控 | 原 PDF 保留并绑定 hash | 已实现 |
| UTF-8 TXT | 按行/段落形成有序 block | 原字节保留并绑定 hash | 用户私有库已实现 |
| 历史 DOC | 必须先由受控离线转换器生成 DOCX，并同时保留原 DOC hash、转换器版本和输出 hash | 禁止静默丢弃 DOC 原件 | 尚不接受直接导入 |

不直接接受历史 DOC 是刻意的 fail-closed 边界：如果只保存转换后的 DOCX，receipt 无法证明它
来自哪个 DOC；如果只保存 DOC，当前构建器又无法确定性解析。未来直接导入必须先把双 hash 和
转换 provenance 加入 release contract，不能用“能打开”代替可追溯性。

## Document IR

`deeplaw.sqlite/v5` 的 `document_blocks` 是当前 Document IR 运行时投影。每个 block 至少保存：

- `block_id`、`document_id`、文档内顺序和文本 SHA-256；
- 页码或段落号、block 类型、样式和可选边界框；
- 选中候选的来源与可选置信度；
- `review_required` 和正交的风险标记；
- 原始规范化文本。

`segments` 记录其 `source_block_ids`，并独立保存 segment 级抽取门禁。一个 PDF 的某一页需要
复核时，只隔离引用该页 block 的 segment；不能再把整份 PDF 的所有条款一并降级。

## PDF 多候选门控

DeepLaw Document Engine 不是“换一个解析器就相信结果”。流程如下：

1. 先检查原生文本层；可靠页面不做无意义 OCR。
2. 只对风险页渲染页面并产生独立 OCR 候选。
3. 可选结构化引擎保留阅读顺序、block 类型、表格文本和边界框。
4. 比较规范化全文，同时比较全部汉字/字母数字 lexical token，并单独检查条款号、数字、日期、
   金额、比例、义务词、否定/例外词及可能改变范围或分组的法律标点。
5. 只有全文一致度达到硬阈值、全部 lexical token、关键语义 token 与法律标点序列完全一致，且
   候选自身没有质量风险，才形成 `machine_consensus`；结构化引擎识别到表格时必须保留人工复核，
   不能用扁平 OCR 全文相似度清除单元格边界风险。
6. 任一路径失败、候选冲突或关键 token 不同，仍选择较完整候选供人工复核，但保持
   `review_required=true`。
7. 人工覆盖必须同时绑定 source PDF hash、渲染页 hash、复核人、时间和声明。

这使结构化解析成为候选生成能力，而不是自动获得权威性的捷径。工具输出只读取结构化 JSON，
不会把引擎生成的 Markdown 当成真源。

## Markdown 派生视图

`v0.3.0` 可以从一个已验证的 immutable release 确定性导出 Markdown：

```bash
deeplaw export-markdown \
  --db "/path/to/release/deeplaw.sqlite3" \
  --output "/path/to/empty-output-directory"
```

每份 Markdown 绑定 release、document 与 source hash；每个导出的 block 和 segment 还同时提供：

- 以真实 `block_id` / `segment_id` 命名的稳定 HTML anchor，可从审阅记录回链；
- `deeplaw.markdown-locator/v1` JSON 注释，供工具读取 page、paragraph、文本 SHA-256、
  `review_required`、risk flags，以及 `segments.source_block_ids_json` 的真实关联；
- 同样内容的可见 locator 行，避免只能依赖隐藏元数据核对原件。

当前 v5 IR 对 DOCX 表格保存 `kind=table_row` 和文档顺序 paragraph，但没有独立的 table ID /
row index 字段；PDF 结构化表格也不保证单元格行号。因此导出器会明确输出
`table_row=null` 及 `row_index_not_stored...` 状态，并回链对应 block anchor，不会把推测出来的
行号伪装成原件定位。page 或 paragraph 未由解析器保存时也写成 `null` / `not-stored`，不会补造。

`index.json` 记录导出文件 hash。存在抽取风险的 block 会显示复核提示，不会被伪装成已核验
文本。导出文件不包含摄取机上的原件绝对路径；排序所用的 release 内部相对路径也不会写入
Markdown。导出目录必须为空，防止旧文件与新 release 混合；任何导出结果都可删除后从
SQLite 重新生成。上述 anchor 与 locator 仍只是 SQLite IR 的确定性投影，不会让 Markdown
成为法源真值、release manifest 或运行时数据库。

## 解析器选择研究

截至 2026-07-16 的工程取舍：

| 方向 | 优点 | 不作为唯一真源的原因 | DeepLaw 决策 |
| --- | --- | --- | --- |
| 通用布局/OCR 引擎 | 中文、表格、公式、扫描件覆盖广 | 模型和后端可能失败；单次输出没有独立见证 | 作为可选结构化候选 |
| PaddleOCR / PP-Structure | 中文 OCR、版面和表格能力强，Apache-2.0 | 仍需模型权重、资源和独立校验 | 后续可作为第二结构化后端 |
| Docling | IR 与来源引用设计清晰，MIT | 中文扫描质量取决于所接 OCR；不是法律效力层 | 借鉴无损 IR 与 provenance |
| Unstructured | 文件 ETL 覆盖广 | 法律层级、逐页审计与确定性不足 | 不作为核心真源 |
| Marker / Surya | 文档转写效果强 | 代码或模型许可边界不适合默认分发 | 不进入默认依赖 |

选择标准不是“谁在单一榜单分数最高”，而是：能否保留结构、能否精确定位、能否绑定原件、
能否被独立候选挑战、失败是否可见、许可是否允许目标分发。第三方依赖和许可见
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。

## 不能做

- 不能把生成的 Markdown 当作 canonical store。
- 不能因解析器命令成功就清除抽取风险。
- 不能让一个文档级 warning 污染所有 segment。
- 不能在候选冲突时用较长文本或较高单一置信度自动胜出。
- 不能在没有原 DOC/输出 DOCX 双 hash 的情况下宣称支持可追溯 DOC 导入。
- 不能把法源解析准确等同于法律效力、真实性或案件适用性已审核。
