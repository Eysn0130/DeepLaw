# 2026-07-14 经侦法源核心包审计

## 审计结论（2026-07-14 初始状态）

逻辑输入包 `Analytix-经侦法源核心包-2026-07-14` 可以作为 DeepLaw 的首个
`candidate`：28 份法源均有 manifest 记录，路径、字节数和 SHA-256 全部匹配，文件容器
可解析且未发现重复正文。

该包不能直接标为 `verified_current`、不能仅凭一次本地 `--activate` 作为生产法律库，也不能
在未完成逐来源许可复核前公开重新分发完整二进制或数据库。初始审计日的主要阻断项是：

1. 1 份 8 页 PDF 当时为纯扫描件，尚无可复核文本；
2. manifest 缺少完整法律时效、文号、发布机关和修订关系字段；
3. 4 份案例仍需再识别风险和再分发条件复核；
4. DOCX/PDF 包含非必要作者元数据，Obsidian 维护笔记包含本机绝对路径；
5. 本次没有在线重新验证全部官方 URL 和当前效力状态。

本审计没有复制法源正文、原始二进制或本机绝对路径到 DeepLaw 仓库。

## 输入范围与方法

只读检查了两个显式输入：

- 2026-07-14 经侦法源核心包；
- Obsidian `Analytix/知识库` 中的维护笔记。

检查内容包括：文件数、大小、扩展名、目录结构、manifest schema、SHA-256、DOCX 容器
结构、PDF 页数/文本层/字体/图像、PDF JavaScript 和附件、基础 PII 模式及本地路径模式。

工具包括系统 `stat`、`find`、`unzip`、`jq`、`shasum`，以及 Poppler 26.05.0 的
`pdfinfo`、`pdftotext`、`pdffonts`、`pdfimages` 和 `pdfdetach`。基础 PII 检查为正则初筛，
不能代替人工隐私复核。

## 文件盘点

### 总量

- 31 个普通文件；
- 全部普通文件合计 `10,782,963 bytes`；
- 28 份 manifest 正文合计 `10,743,549 bytes`；
- 10 DOCX，合计 `489,292 bytes`；
- 18 PDF，合计 `10,254,257 bytes`；
- 辅助文件为 README、manifest 和 `.DS_Store`。

`.DS_Store` 不属于语料或发布元数据，必须由 `.gitignore`、摄取 allowlist 和 release
构建器共同排除。

### 目录分布

| 逻辑目录 | 文件数 | 字节数 | 格式 |
|---|---:|---:|---|
| `00-说明与清单` | 2 | 20,978 | JSON 1、Markdown 1 |
| `01-核心法源` | 4 | 486,871 | DOCX 3、PDF 1 |
| `02-金融与非法集资` | 4 | 654,252 | DOCX 2、PDF 2 |
| `03-数据与网络` | 3 | 76,851 | DOCX 3 |
| `04-案例参考` | 4 | 1,260,071 | PDF 4 |
| `05-办案程序与证据` | 4 | 6,112,782 | DOCX 1、PDF 3 |
| `06-反洗钱、支付与主体穿透` | 8 | 2,129,357 | PDF 8 |
| `07-罪名专题` | 1 | 23,365 | DOCX 1 |

目录字节数不含根目录下的 `.DS_Store`。

## Manifest 完整性

`download-manifest.json` 声明 28 份文档：10 DOCX、18 PDF。逐文件复算结果：

- 缺失文件：0；
- 路径未登记正文：0；
- manifest 路径无对应正文：0；
- 字节数不一致：0；
- SHA-256 不一致：0；
- 重复 SHA-256 组：0。

28 份文件均有 `path`、`title`、`format`、`officialSource`、`byteSize` 和 `sha256`。
官方 URL 覆盖国家法律法规数据库、司法部行政法规库、人民银行、法院和监管机构等官方
站点。这是较好的来源基础，但 URL 存在不等于当前仍可访问，也不等于文件仍为现行版本。

### 生产 schema 缺口

现有文档对象只有以下可选业务字段：`effectiveDate`、`caseId` 和 `note`。统计为：

- `effectiveDate` 有值 17/28；
- `caseId` 有值 4/28，均为案例；
- `note` 有值 8/28。

生产 release 还需要结构化补齐：

- `issuer`、`hosting_authority`、`document_number`、`jurisdiction`；
- `promulgated_on`、`effective_from`、`effective_to`、`status`；
- `cites`、`amends`、`repeals`、`replaces`、`implements`、`exception_to`；
- 状态依据 URL、复核者、复核时间和复核结论；
- `copyright_class`、二进制/文本再分发范围和 PII 复核状态；
- parser/OCR 版本、定位质量和人工抽样记录。

包说明已经指出至少两类必须配套处理的版本关系：刑法正文与后续修正案，以及原规章与
2025 年修改决定。DeepLaw 不能让 LLM 在查询时临时拼接这些关系；必须先建立可审计的
版本图并由人工确认。

## PDF 审计

### 整体结果

- 18 份 PDF 全部可由 Poppler 解析；
- 共 472 页，最长 193 页；
- 全部未加密；
- 未检测到嵌入附件；
- 未检测到 PDF JavaScript；
- 17 份存在可抽取文本层；
- 17 份文本中未发现替换字符、Private Use 字符或异常控制字符。

文本型 PDF 的非空中文字符比例约为 0.786 至 0.919，说明编码抽取初步正常，但该指标
不能证明条款、标点和跨页顺序绝对准确，仍需 locator 和抽样比对。

### 纯扫描文件

以下文件为明确的纯扫描 PDF：

```text
05-办案程序与证据/关于进一步规范刑事诉讼涉案财物处置工作的意见.pdf
```

特征：8 页、抽取字符数为 0、无字体对象、每页一张 `2480 x 3508` 图像。

该文件必须保持 `needs_ocr`，直至完成：

1. 本地 DeepLaw Vision/OCR 派生；
2. 每页题名、文号、日期、正文和页序人工检查；
3. OCR 输出、页面坐标、工具版本和 hash 记录；
4. OCR 后 PII 检查；
5. 与官方来源或独立权威文本比对。

其余含图片的 PDF 中，图片是每页 `74 x 74` 的图标和透明蒙版，同时存在完整文本层，
不能仅根据“图片数量大于零”误判为扫描件。DeepLaw 应同时评估文本覆盖率、字体和页面
主图尺寸，优先原生提取。

## DOCX 审计

10 份 DOCX：

- 全部通过 ZIP/OOXML 容器检查；
- 未发现宏、外部关系或 OLE 嵌入对象；
- 未发现正文表格和媒体文件；
- 共约 246,555 个正文字符；
- 可识别约 1,745 个条文标记、366 个编章节标记；
- 一份文档存在 2 条真实脚注。

结构风险：所有文件都没有可用的 Heading 样式；9 份没有使用段落样式，另一份也只有
一个普通样式。`docProps/app.xml` 的缓存页数、字数和段落数存在明显不一致，不能作为
结构真相。

因此 DOCX 摄取必须：

- 按 OOXML 正文顺序读取；
- 用确定性法条编号和章节标记建树；
- 保留段落 ordinal、run/字符位置和脚注；
- 对跨段法条、修正案项目和超长段落做专门 fixture；
- 不依据 Heading 或缓存页数生成 citation。

## Obsidian 维护笔记

审计目录当前仅有一份约 10 KB 的下载与更新指南，没有 DOCX/PDF 法源正文。它正确记录了
公共法源与案件私有材料应隔离，也明确说明当前不是自动同步程序。

该笔记同时包含：

- 7 处本机用户绝对路径模式；
- 3 个 `file:///` 本地链接；
- 文件题名、来源站点和本地维护步骤。

这不是凭据泄漏，但会暴露本机用户名和目录布局，且不可移植。该笔记只能作为运维文档
输入，不能进入法条 corpus；公开版本应把本机路径替换为占位符或配置变量。

DeepLaw 不得监听或遍历整个 Obsidian Vault。未来 Vault 中可能包含案件笔记，摄取必须使用
显式单文件 allowlist。若生成 Obsidian 导航，应由 release 元数据单向生成，不得将 Vault
内容自动回写公共法律库。

## 安全、隐私与元数据

基础模式扫描在可抽取正文中没有发现 email、中国手机号或中国身份证号。Manifest 中出现
的长数字来自官方 URL 条目 ID，不是正文账户号。该结果有三项限制：

- 纯扫描文件在 OCR 前不可扫描；
- 规则不能识别所有姓名、地址和事实组合；
- 公开案例即使使用化名，也可能通过事实细节再识别。

元数据检查发现：

- 10/10 DOCX 含非空 creator 和 lastModifiedBy；
- 13/18 PDF 含非空 Author；
- PDF 还普遍包含 Creator、Producer 和时间元数据。

为保持来源 hash，不应修改原件。公开 API 和派生文本应只投影白名单业务字段，排除作者、
本机路径和非必要工具元数据。4 份案例在公开分发前必须人工复核隐私和来源条款。

## 许可和再分发风险

本次只确认了官方来源和本地文件完整性，没有作出法律许可结论。“公开下载”不能自动推出
可以把原 PDF/DOCX、网站版式、标识、案例库编排或完整提取数据库重新公开分发。

在逐来源确认之前：

- DeepLaw GitHub 仓库只提交代码、schema、来源 URL、hash 和不含正文的测试；
- 原件留在本地候选区或经过授权的制品存储，不进入 Git history；
- release manifest 使用 `redistribution_status: not_assessed`；
- 不公开可还原完整正文的 SQLite release；
- 代码或上游解析器授权不视为第三方法源文件授权。

## 发布决定

### 2026-07-15 历史本地候选构建跟进

DeepLaw 已用校验后的 28 份输入完成一次本地、不可公开分发的候选构建：

- release：`lawrel_77cf88a46c1324ccb87d9dcda004d27a`；
- 文档/segment：28 / 3268；
- extractor：OOXML 10、pypdf 17、本地 Tesseract OCR 1；
- database SHA-256：
  `9da945895cf9ccbaa6779efe053eeb1e191248827516087fc75b749616da5c69`；
- release 状态仍为 `temporal_status: requires_human_review`、
  `redistribution_status: not_assessed`。

质量门禁识别出扫描 PDF 的隐藏 OCR 文本存在 0.873 的汉字间空格比率，拒绝把字符数误当
合格文本层，并改用本机 Tesseract 5.5.2 处理 8 页。派生文本约 2998 字符，证据卡会携带
`extraction_review_required: true` 和原文本层/OCR 警告。首页已视觉核对；尚未逐页逐字复核，
因此这一步关闭了“不可检索”的工程缺口，没有关闭内容批准、隐私或法律效力闸门。

以上 release ID、数据库 hash 和计数只描述当时生成的 `deeplaw.sqlite/v2` 不可变候选快照。
它早于随后使用的 `deeplaw.sqlite/v3` provenance schema，没有记录 OCR 渲染器版本、DPI、
语言、page segmentation mode 或独立 extracted-text hash，也没有坐标/置信度。当时代码会在
新构建中记录 Tesseract 与 `pdftoppm` 版本、上述 OCR 配置和 extracted-text hash，但不会追写
旧 release，且 v3 仍未保留坐标/置信度。当前 v4 已记录页级 OCR 置信度与一致度，但仍不保留
词级坐标。因此本节不能作为当前源码已通过 runtime 或 G2 门禁的证据；必须以当前 v4 构建、
复核和验证结果为准。

### 2026-07-15 历史 v3 本地候选

随后完成的 v3 用户级候选为：

- release：`lawrel_d61619b2b1e4c2bf9e3124fc9be3df06`；
- storage schema：`deeplaw.sqlite/v3`（SQLite 3.50.4）；
- 文档/segment：28 / 3268；
- extractor：OOXML 10、pypdf 17、本地 Tesseract OCR 1；
- database SHA-256：
  `354da5951febf8a80c332f8f84535015bae2366dce6d1972946ef292ee1414d5`；
- source manifest SHA-256：
  `64a9181a36572feea8609dca9b794fab8aeb99989a0806398aa6f5084322a749`；
- 状态仍为 `temporal_status: requires_human_review`、
  `redistribution_status: not_assessed`。

该历史扫描件记录 `tesseract 5.5.2; pdftoppm version 24.02.0`、300 DPI、
`chi_sim+eng`、PSM 3、8 页、2979 个提取字符和 extracted-text SHA-256
`05e1f8bedb7e912dab22414cf99697bbbc8ef75716df359930cc9551d583be87`。它仍不含词级坐标或
置信度，也未完成逐页逐字人工金标；因此 v3 关闭的是 provenance 和运行时可复核缺口，不是
法律内容批准闸门。本节只保留为迁移历史，不代表当前活动 release。

### 2026-07-15 当前 v4 / release v2 本地候选

当前源码以 hash-bound AI precheck overlay 完成重建并激活以下本地候选：

- release：`lawrel_0a7b7cb0a0fe5e3649a6b85889083351`；
- release/storage schema：`deeplaw.release/v2` / `deeplaw.sqlite/v4`
  （SQLite 3.50.4）；
- 文档/segment/relation：28 / 3234 / 111；
- extractor：OOXML 10、`deeplaw-vision-consensus` 18；17 份 PDF 以原生文本为主，
  风险页按页启用 OCR；
- database SHA-256：
  `5443207e6118c46d7df251c73f794ef6342f42f973ec29ea88f6fe1beddb46ed`；
- source manifest SHA-256：
  `64a9181a36572feea8609dca9b794fab8aeb99989a0806398aa6f5084322a749`；
- review overlay SHA-256：
  `9e9a67ca0e12282d610e192c984b89f2ade63a394b52324e37a404aa84288af2`；
- 状态：`temporal_status: partially_verified`、
  `redistribution_status: restricted`、`reviewer_kind: ai_precheck`。

构建报告保留 36 项页级风险警告；5 份 PDF 的少数风险页选择 OCR，其中纯扫描的 8 页文件
全部选择 OCR，记录 5121 个提取字符、逐页 OCR 置信度/原生一致度、页面图像与文本 hash，
extracted-text SHA-256 为
`bff5acbdbe05975f779587e6f13ec247cfdc115cba95eb5238ad34e556eb93e9`。
本次只对第 1 页和第 8 页进行 AI 视觉抽样：题名、文号、首尾页版式可辨，第 8 页主要是印发
信息且字符很少，与自动风险标志一致。该抽样不是逐字校对、人工 attestation 或官方认证，
所以该文件仍为 `review_required: true`。

当前 runtime 已验证 release/database/segment/receipt hash，精确命名的现行性查询不会注入
无关 FTS 候选；已知非当前文件被排除，未通过完整人工时效审核的候选只进入
`uncertain_evidence`。这些是工程门禁结果，不是法律效力意见。

| 项目 | 结论 |
|---|---|
| 作为本地候选输入 | 通过 |
| 路径、大小和 hash 完整性 | 通过 |
| 文件容器基础安全 | 通过 |
| 全部正文可检索（当前候选） | v4 候选构建通过；风险页仍需逐页人工复核 |
| 法律时效和版本关系 | AI precheck 已结构化；完整人工复核未通过 |
| 案例隐私 | 未通过，需人工复核 |
| 二进制/数据库公开再分发 | 未通过，许可未评估 |
| 生产默认激活 | 未批准 |

“未通过”在此表示发布前置工作未完成，不表示来源本身无效。

## 整改清单

- [x] 以 hash-bound review overlay 将原始 manifest 投影为 DeepLaw 法律版本 metadata；
- [ ] 补齐 28 份文档的发布机关、文号、效力状态和版本关系；
- [x] 对纯扫描 PDF 执行一次本地 OCR，并保留当时的 Tesseract 版本和待人工复核标记；
- [x] 用 release v2 / SQLite v4 重建候选，记录解析器、OCR 配置和页级 evidence hash；
- [ ] 对该 OCR 派生逐页逐字复核并完成 PII 检查；
- [x] 为 DOCX 法条树和脚注建立确定性测试；
- [ ] 为修正案结构和版本血缘建立人工金标测试；
- [ ] 在线复核官方 URL、文件标题、hash 和当前状态；
- [ ] 对 4 份案例完成 PII/再识别和再分发复核；
- [ ] 对每个来源记录许可结论；
- [ ] 从公开文档移除本机路径和 `file:///` 链接；
- [x] 运行首轮精确条款、时点过滤、泛词和上下文预算回归评测；
- [ ] 增加人工金标的无命中、历史版本、字符区间和 locator 盲测；
- [ ] 在全部适用闸门通过后生成新的不可变 release 并签发批准记录。

## 可重复的只读验证

以下命令使用调用者提供的环境变量，不把本机路径写入仓库：

```bash
ROOT="${DEEPLAW_SOURCE_ROOT:?set DEEPLAW_SOURCE_ROOT}"
MANIFEST="${DEEPLAW_SOURCE_MANIFEST:?set DEEPLAW_SOURCE_MANIFEST}"

find "$ROOT" -type f | wc -l
find "$ROOT" -type f \( -iname '*.docx' -o -iname '*.pdf' \) | wc -l

jq -r '.documents[] | [.sha256, .path] | @tsv' "$MANIFEST" |
while IFS=$'\t' read -r expected relative_path; do
  actual="$(shasum -a 256 "$ROOT/$relative_path" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf 'HASH_MISMATCH %s\n' "$relative_path"
  fi
done

find "$ROOT" -type f -iname '*.docx' -print0 |
while IFS= read -r -d '' file; do
  unzip -tqq "$file" >/dev/null || printf 'INVALID_DOCX %s\n' "$file"
done
```

绝对路径检测只打印文件名，不应把匹配行提交到日志：

```bash
rg -l 'file:///|/Users/|[A-Za-z]:\\\\Users\\\\' docs plugins contracts README.md
```

## 审计限制

- 未在线重新下载并逐文件与当前官网比较；
- 未对全部页面进行视觉渲染比对；
- 未完成正式法律时效、许可或隐私意见；
- 已对纯扫描 PDF 做本地 OCR 和首尾页 AI 视觉抽样，但未完成 8 页逐字人工复核；
- 已用当前 release v2 / SQLite v4 schema 重建；该结果仍是本地、受限、AI precheck 候选；
- 文本质量统计只能发现明显编码问题，不能证明法律文本逐字无误。

后续发布必须引用新的复核证据，不得把本快照审计当作永久有效的现状证明。
