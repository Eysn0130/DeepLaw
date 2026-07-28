# DeepLaw v0.7 本地施工交付审计（GA 前历史记录）

> 本文冻结的是正式发布前的 dirty-worktree 施工证据和旧 `0.6.0` 构建 hash，不是 v0.7.0
> Release manifest。正式商业结果见 `docs/V0_7_ACCEPTANCE_MATRIX.md` 和 GitHub Release 中的
> `commercial-release-manifest.json` / `post-release-verification.json`。

审计日期：2026-07-28（Asia/Shanghai）
候选线：`0.7.0-unreleased`
Python 包版本：`0.6.0`
基线提交：`e0f1fe3ff01d3026df12673d57c69014c2c4dca4`
结论：**本地可施工范围已经形成完整工程候选；正式商业发布与“整体最优”门禁未通过。**

本报告是包外施工证据，已从 sdist 排除，以便记录 sdist 自身 SHA-256 而不产生自引用。
规范真相仍是源码、测试、JSON Schema、SQLite migration 和 `uv.lock`。

## 总体判定

Identity v2、Source IR、many-to-many evidence、Knowledge Lineage、temporal relation、Retrieval
Fabric、Query Plan、token-aware Capsule、Inbox/Feedback、Golden CLI、Workbench、Obsidian/Canvas、
Skill Factory、可靠性和供应链工具均已落到当前工作树，并由本地测试或机器诊断覆盖。Agent MCP
仍然只读，canonical state 仍然只在本地 owner-only Vault 中。真实 Codex CLI 的双插件
marketplace/install/remove/readd 生命周期已在隔离临时配置中通过；该诊断不含模型或 MCP 任务流。

当前不能把该工作树称为正式 `v0.7.0` 或商业发布成品，原因不是待补一个功能开关，而是下列
证据只能由后续真实环境或独立方产生：17 项同协议具名基线结果、冻结候选上的统计门禁、真实
Windows/三操作系统矩阵、三种 Agent 宿主端到端、两个秘密 held-out、两家独立机构签名、最终
tag 的 Sigstore 签名与 provenance/SBOM attestation。开发团队不得伪造这些证据。

## 1. 当前真实基线

施工前事实记录在 [`V0_7_BASELINE_AUDIT.md`](V0_7_BASELINE_AUDIT.md)：

| 项目 | 施工前 | 当前工作树 |
| --- | --- | --- |
| Git | `main` / `e0f1fe3ff01d3026df12673d57c69014c2c4dca4`，施工前 clean | 同一 HEAD 上的未提交候选施工，尚未冻结 |
| 包版本 | `0.6.0` | 仍为 `0.6.0`；未提前升级 |
| 测试基线 | 395 tests passed | 见第 24 节最终复测记录 |
| 默认检索 | 单 lexical 路径 + 一次 reviewed relation expansion | 统一 Query Plan + 多通道 Candidate Discovery + 中央 Admission + token-aware Capsule |
| CLI | 需要复制内部 ID/hash | 根级 Golden Path；高级命令保留稳定 JSON/JSONL |
| Identity | 绝对路径、内容、编译和治理部分耦合 | collection/logical path、bytes、compilation、proposal、Knowledge、governance 分离 |
| 规模证据 | v0.6 合成 control diagnostic | 实际 100k 与 1m Retrieval Fabric 施工诊断 |

施工前的 Discovery、最小 Obsidian 投影和外部比较均未被改写成历史上的“已支持”。历史快照仍按
各自源码/版本绑定保存。

## 2. 上游研究矩阵

[`UPSTREAM_CAPABILITY_MATRIX.md`](UPSTREAM_CAPABILITY_MATRIX.md) 固定审阅了 RAGFlow、Microsoft
GraphRAG、LightRAG、Graphiti、Mem0、Letta、Letta Code、Cognee、MemOS、PageIndex、OpenKB、
WikiGraph、Obsidian Help、JSON Canvas，以及 MinerU、Docling、MarkItDown 和 PaddleOCR。每项记录
commit/release、license、强项、限制、runtime/network/data boundary、benchmark evidence 和
DeepLaw 采纳决定。

矩阵中的上游自报 benchmark 不转移为 DeepLaw 结果；不同语料、模型、Token、硬件和裁判结果
没有被强行归一化。

## 3. 实际复用代码与许可证

| 项目 | 实际采用方式 | 许可证/发布边界 |
| --- | --- | --- |
| 上述知识平台 | `protocol/concept reimplementation` | 没有复制或 vendoring 其源码；不是 DeepLaw runtime dependency |
| MinerU `3.4.4` | fixed local closed subprocess | MinerU Open Source License；模型显式准备且不随 wheel 分发 |
| JSON Canvas 1.0 | 实现公开文件格式 | 格式仓库 MIT；未复制 Obsidian 专有应用代码 |
| Tesseract / Poppler / LibreOffice | 可选本机进程集成 | 不随包分发；精确再分发义务见 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) |
| Python runtime dependencies | 锁文件依赖 | 进入 SBOM、license inventory 与 dependency audit |

[`UPSTREAM_REUSE.md`](UPSTREAM_REUSE.md) 保留文件级研究坐标；当前不存在需要新增 Copyright/Notice
的上游复制文件。任何未来 direct reuse 或 vendoring 仍需单独记录 exact file、commit、license、
SBOM 和测试。

## 4. Identity v2

`knowledge-identity/v2` 已分离：

- `collection_id + normalized logical path → source_key`，绝对路径只作本机 hint；
- exact bytes/media/origin commitment → immutable `source_revision_id`；
- adapter/parser/config/IR/fragment inventory → `compilation_id`；
- extractor/model/prompt/proposal/ref graph → `proposal_set_id`；
- 稳定 `knowledge_key` 与不可变 `asset_revision_id`；
- 独立追加 `governance_revision`；
- 稳定 `relation_key` 与不可变 `relation_revision_id`。

Schema 位于 [`knowledge-identity.v2.schema.json`](../contracts/knowledge-identity.v2.schema.json)，
实现位于 `knowledge_identity.py`。测试覆盖项目整体移动、不同 clone root、collection 隔离、
rename/move、同字节不同 compiler、仅 sensitivity 改变和 Identity snapshot/integrity replay。

## 5. Migration

v0.6 → v0.7 migration 是 additive：先建立并验证 owner-only backup，再安装 v2 tables、回填
legacy binding、重建 tokenizer/index identity、写入审计和 Identity snapshot。失败在事务内关闭；
显式 rollback 从已验证 backup 恢复。

`tests/test_identity_migration_v060.py` 使用自包含 v0.6 fixture 验证 plan/apply/verify/rollback、
旧 source/fragment/review order、搜索兼容和审计连续性。可选真实 wheel 用例只接受历史 manifest
固定的 exact v0.6 wheel SHA-256；当前 candidate 同样保留 `0.6.0` 版本字符串时不会被误认成历史
artifact。迁移不会把 source-free legacy proposal 伪装成 v2 source-authoritative knowledge。

## 6. Source IR

统一 [`source-ir.v1.schema.json`](../contracts/source-ir.v1.schema.json) 记录 node/logical node、父子、
ordinal、type、title/text、locator/span、content hash、adapter/version、quality flag 和 instruction
risk。当前闭合 adapter 覆盖：

- Markdown/TXT、HTML、PDF、DOCX、PPTX、XLSX、EPUB；
- JSON、JSONL、YAML、TOML、CSV、TSV；SQLGlot statement/CTE/table/column AST；
- Python AST；JavaScript/JSX、TypeScript/TSX、Java、Go、Rust 的 exact-pinned Tree-sitter AST；
- conversation、tool result 和结构化记录。

Tree-sitter core/grammar 版本进入 compilation identity；代码保留 module/class/function/method、嵌套
symbol path、line span、doc comment、imports 和 call references。语法恢复及超 size/node bound 后的
lexical fallback 都是显式 quality flag。SQLGlot 版本与 generic dialect 同样进入 compilation
identity；parse failure、recursion exhaustion 和超界的 bounded lexical fallback 被明确标记。
`deeplaw-source-adapters/4` 在内容提取前验证完整 OOXML/EPUB archive 与 relationship inventory，
限制 XML byte/node/depth，并验证 XLSX row/cell/shared-string/formula/merged-range inventory。PDF 默认路径保留
page/layout block，真实 MinerU 诊断只覆盖一页英文文本，不覆盖 OCR、表格、图片、损坏文件或
多语言版面；这些限制不能被“suffix accepted”掩盖。

### 6.1 显式 HTTPS / 本地 exact-Git Source Snapshot

[`source_connectors.py`](../src/deeplaw/source_connectors.py) 与
[`source-snapshot.v1.schema.json`](../contracts/source-snapshot.v1.schema.json) 已实现 owner-only、
hash-bound、一次性连接器快照。HTTPS preflight 不解析 DNS、不联网、不写快照；capture 必须显式
`--confirm-network`，只接受公共 DNS、443、无 credentials/query/fragment 的 canonical URL，逐跳
重新执行 SSRF 门禁并固定 endpoint/TLS hostname，限制 5 次 redirect、identity encoding 和
64 MiB，可要求 expected SHA-256，且 governance 强制 `untrusted`。本地 Git 只接受现有非 symlink
目录、稳定 repository ID 与完整 40/64 hex commit；只执行 bounded `rev-parse` / `ls-tree` /
`cat-file`，禁用 replace/lazy fetch/protocol/prompt，不 clone、不 checkout，并复核 blob object ID。

[`knowledge-ingest-job.v2.schema.json`](../contracts/knowledge-ingest-job.v2.schema.json) 把 snapshot、
canonical origin、collection 和 logical path 绑定到可恢复 job；运行前重新验证 manifest、Vault
binding、identity、权限、文件 inventory、size/hash。v1 job 仍可读取并在下一次持久化时规范化为
v2。Git canonical origin 不含绝对本地路径；两类 snapshot 都不注册 `sync --watch`、不进入 MCP
写面、不绕过 proposal/quarantine 与人工 review。

本机证据包含真实本地 Git commit 的 Golden `add → review → recall`、HTTPS 无网络 preflight、
DNS/TLS endpoint pinning 的闭合 transport test、hash/manifest/content/tamper/fail-closed tests。
当前执行环境把公共域名解析到 `198.18.0.0/15` 测试网段，产品按设计拒绝，因此真实公共 HTTPS
endpoint/certificate/redirect smoke 仍需在冻结候选的正常公网环境执行，不能用 mock 冒充。

Source Tree 的 get/list/search/trace 只返回可回到 Source IR/原件验证的结构；生成 summary 或
reasoning trace 不成为 canonical truth。

## 7. Many-to-Many Compiler

Compilation 的 fragment inventory 与 Proposal Set 的 proposal/ref graph 已分离，不再要求
`fragment_count == asset_count`。当前支持 0..N proposal、一个 fragment 支持多条 proposal、
一条 proposal 引用多 fragment/多 source、Reference 与 Typed Claim 共存，以及仅以 proposed
derived Asset 形成 cross-document synthesis。

Review Manifest 分别绑定 `source_revision_id`、`compilation_id`、`proposal_set_id`、fragment/
proposal/ref graph inventory SHA-256 与 quarantine count。`deterministic-v2`、`local-model-v1`、
`external-model-explicit` 和 `off` 都只产生 proposed/quarantined 结果；只有人工 review 可 active。

Typed Compiler scorer 已建立 precision、recall、F1、hallucinated/unsupported claim rate、
source-span correctness、duplicate rate、review acceptance 和 cross-document synthesis 指标。
checked synthetic fixture 只验证计分语义，不能作为 compiler 质量结论；真实 reviewed gold suite
仍属发布前证据缺口。

## 8. Knowledge Lineage

Lineage 支持 `new`、`unchanged`、`modified`、`renamed`、`moved`、`split`、`merged`、`deleted`、
`ambiguous`。匹配证据组合 source key、logical node、parent path、normalized content、source span、
symbol、邻近节点、手工 mapping 和 prior knowledge key。

modified 不继承批准；split/merge/ambiguous 进入独立人工 review；deleted 退出 current Context；历史
Capsule、审计和 Feedback 仍可按 stable knowledge key 验证或定位后继 revision。新增 closed
`knowledge-lineage-review/v1` workflow：只接受 exact source-bound Identity v2 revisions，把同一
cross-key mapping 写到所有涉及的 Knowledge Key，记录 reviewer/reason/source refs，幂等 replay，
不创建或激活知识，也不继承批准。高级 CLI 接受 exact Asset ID；Workbench 以可见行号提供普通无 ID
操作面。Identity snapshot 会检测对 mapping 的数据库篡改。

## 9. Temporal Graph

Relation revision 记录 evidence refs、status、event/valid/observed/review/ingest time。自动关系只能
是 proposal；current Fabric 只使用两端 active、关系 active、evidence source active/reviewed 且
非 restricted 的 relation revision。source-free compatibility edge 只保留历史检查能力，不能进入
当前 Fabric/Capsule。

`current`、`past`、`as-of` 已实现。来源切换后，独立的 relation carry-forward planner 会按两端
lineage 和 exact evidence successor 重算关系：unchanged 生成 inactive `carry_forward` candidate，
modified/renamed/moved 生成 inactive `full_review` candidate，deleted/split/merge/ambiguous、无唯一
后继或 evidence 映射不唯一时失败关闭。Golden Review 和 Workbench 都可直接处理队列；旧批准绝不
继承，只有再次明确 approve 后新的 relation revision 才进入 current graph。planner 还会识别已审核
的 cross-key split/merged/ambiguous mapping 并阻断受影响端点。契约、实现和回归分别为
`contracts/relation-carry-forward.v1.schema.json`、`src/deeplaw/relation_workflow.py` 和
`tests/test_relation_workflow.py`。时间匹配明确不等于事实或法律适用。

## 10. Retrieval Fabric

统一通道包括 exact Asset/URI、Knowledge Key、semantic key、explicit phrase、fielded lexical、
Source Tree、reviewed graph、temporal、feedback、显式 Dense 和可选 local reranker。所有通道只做
Candidate Discovery；中央 Admission 再检查 status/review/expiry/sensitivity/source integrity/
lineage/time/scope/policy/case-data boundary。

模式为 `auto`、`exact`、`lexical`、`semantic`、`tree`、`graph`、`temporal`、`hybrid`、`global`。
Trace 记录每通道候选、rank、fusion、rerank、排除原因、source/duty coverage、gap 和最终选择，
并可校验 plan/trace tamper。MCP 的 search/context 已复用同一 Fabric，但不暴露 operator-only trace
或写接口。

`retrieval-fabric/2` 在普通 lexical 完全无候选时，从有界 FTS prefix 集合执行 exact ASCII 单编辑
距离后过滤（含相邻转置），并保留 `query→candidate` trace；距离更远的噪声仍返回 no-answer。
reviewed graph 使用 visited set、最多两跳、20 项 frontier 和 channel budget，每条边仍经 exact
source evidence admission。回归还验证 `global` 跨来源选择能生成并重新验证 token-bounded Capsule。

## 11. Query Duties

Query Plan 稳定绑定 intent、duties、channel budgets、filters、temporal scope、fusion/ranking/
reranker/tokenizer profiles 和 implementation revision。当前 Duties 覆盖 constraints、current
decisions、required procedures、definitions、known lessons、recent changes、applicability、exceptions、
conflicts、open questions、missing evidence 和 counterevidence。

未满足 Duty 保留为 gap；来源内 prompt injection 或 directive 不参与 Query Plan 生成，也不能提升
为 Agent 指令。

## 12. CJK、英文与代码召回

查询层使用 NFKC、全半角统一、小型繁简兼容表、中文 stop terms/2-3 gram/有限同义词扩展、法条号
归一、长查询头中尾确定性取样。英文和代码路径支持保守 stemming、quoted phrase、hyphen/
underscore/dot components、camelCase、symbol path、version/error-code components；精确标识优先于
source-wide common term。

回归覆盖繁体查询、CJK/英文/代码混合、长查询尾实体、多实体、acronym、有限同义词、ASCII 单编辑
typo、exact semantic identity、phrase、no-answer、source swamp、current/as-of、两跳 graph、global、
temporal 和 lifecycle。多编辑/中文错别字、完整繁简转换、proximity parser 和大规模自然语言同义词
质量仍须在冻结 held-out 上证明，当前不得宣称已解决。

## 13. Hybrid 与 Reranker

`rrf-duty-diversity/1` 使用版本化 deterministic weights、RRF、source diversity、knowledge-key
deduplication 和 Duty/type priority。Dense 只有显式提供且 exact-model-bound 的 Discovery Index 才
运行；默认 Context/MCP 不会联网、下载或静默 fallback。

本地 reranker manifest 固定 executable/argv、model/revision/files SHA-256、candidate/input/output
上限和 timeout。子进程 stdout/stderr/时间有界；输出必须是既有 candidate 的精确 permutation，
不能加入证据，也不能改变 authority。公开开发消融曾因 irrelevant rate 上升而拒绝 Dense 默认
激活，这一失败证据被保留。

## 14. Token Context

Capsule 保留 item、character、source-ref 和 serialized-payload hard limit，并新增 tokenizer profile、
version、`max_tokens`、`selected_tokens` 和 `exact|estimated|unavailable` 计数模式；estimated 不会标成
exact。选择同时考虑 Duty coverage、source diversity、counterevidence、provenance、token cost、
redundancy、staleness 和 noise。

每个 source-bound item 至少保留一个 compact exact ref。verify 会重放 audit/state/source/hash/
query-plan/budget 绑定，并拒绝 stale、missing source、tamper 或超预算 Capsule。

## 15. Agent Inbox

物理隔离 Inbox 接收有界 `.dlproposal`、`.dlfeedback`、`.dlrun`、`.dleval`，验证 schema、重复 key、
size、SHA-256 和 vault binding。promote 会先把 artifact 原始字节登记为 untrusted Source Revision，
再创建 Identity-v2-bound quarantine；reject 也留下审计。MCP 没有 submit/promote/reject/write 工具。

## 16. Feedback Learning

Capsule-bound Run Record 不从命令退出码推断 task success。Feedback 标签覆盖 helpful、irrelevant、
harmful、stale、missing、incorrect relation 和 budget failure，并绑定 Run/Capsule/Asset inventory。

Ranking Profile 支持 train/evaluate/activate/rollback；profile 只改变 channel weights/diversity/type
priority，不改变 authority。激活要求完整 regression suite 且 safety/noise/update/forgetting 门禁
通过。当前仅有 deterministic local regression，尚无冻结自然语言 held-out 训练收益结论。

## 17. CLI 与 TUI

根级 Golden Path 为 `init → add → review → recall → explain`，无需复制内部 ID/hash。另有 `sync`、
`feedback`、`status`、`doctor`、`open`；高级 `deeplaw knowledge ...` 保留 `human|json|jsonl`、
`--quiet`、`--no-color`、dry-run/progress/resume/cancel/retry 等控制。

Source 管理命令新增 normalized logical-path `--alias` 与 `--active` / `--latest`。审核后的 rename/move
保留历史 alias 到同一 Source Identity 的解析；alias 跨 identity 复用会失败关闭。`source diff`
可以直接比较某 alias 的最近两个 revision，不再要求普通更新检查复制内部 source ID。

`deeplaw open` 在 TTY 中启动本地 curses Workbench；非 TTY 返回同一 bounded snapshot。面板覆盖
source/tree/diff、review、search/recall/explain、lineage、可见行 cross-key mapping、relations/history、
Capsule、feedback、health 和 benchmark boundary。approve/reject batch 在一个事务内提交，重复选择
失败关闭，任一决策失败会整体回滚；quarantined proposal 还要求独立风险确认。它不开 socket、没有
遥测、复用 CLI service layer。真实大队列可用性与键盘/辅助功能研究仍为 P2。

## 18. Obsidian 与 Canvas

确定性 projection 输出 source、knowledge、concept、decision、constraint、procedure、experience、
question、relation/history、Capsule、feedback Markdown，以及 JSON Canvas。页面带 YAML properties、
revision/source links、review/time、supersedes、backlink 和 DeepLaw URI。

projection 永远不是数据库。反向编辑只执行 `diff → quarantined proposal → review`，不能覆盖 active
Asset 或继承批准。Obsidian 专有代码未复制。

## 19. Skill Factory

`skill build/verify/install/update` 生成并检查 read-only bundle，绑定 vault revision、Knowledge
Key/revision、source hash/ref、scope、token budget、generated files、tests 和 manifest SHA-256；输出
覆盖 Codex、Claude Code、OpenCode 和通用 Agent Skill 约定。外部 bundle 默认 quarantine；Skill
不包含 ingest/review/approve/delete/admin 命令。

### Codex real-host plugin lifecycle

`benchmarks/hosts/run_codex_plugin_smoke.py` 使用真实 Codex CLI 和全新的临时 `CODEX_HOME`、`HOME`
及 XDG roots，只传闭合的最小环境，不注入用户配置或凭据，也不发起模型/API 请求。它发现两个
marketplace 产品，依次安装、移除、重新安装每个插件，确认另一产品保持安装，并把四次 cache
inventory 与 source 的每个
相对路径、byte size 和 SHA-256 精确比较；最后移除全部插件。原始 CLI 流不落盘，只保留 SHA-256、
byte count 与去路径化事实。

真实报告为 `benchmarks/hosts/codex-plugin-smoke-2026-07-28.json`，并明确固定
`scope=plugin-lifecycle-only`、`full_host_acceptance=false`、`claim_eligible=false`。当前没有 OS 网络
sandbox，也没有冻结 wheel、模型/session activation、tool discovery、recall/context/verify、Explain、
restricted exclusion、read-only、proposal/feedback 或 inactive-zero-impact 证据，因此不关闭完整宿主门禁。

## 20. Windows ACL

已实现原生 Windows owner SID、Users、Everyone、inheritance、reparse point、junction、source、
model 和 index 检查及 hardening；PowerShell 调用使用 encoded closed script、最小环境和 bounded
subprocess。纯 payload 测试在本机通过；native ACL 与 junction 测试只在 `os.name == "nt"` 运行。

当前 macOS 不能证明 Windows 结果，状态保持 **External verification pending**。CI workflow 已含
`windows-latest`；tagged release workflow 也要求 Linux/macOS/Windows 全量 test、build 与 fresh-wheel
先通过才允许签名，但只有最终冻结候选的真实成功 run 才能关闭门禁。

## 21. 10 万与 100 万规模结果

两份报告都实际运行 Identity v2 compile/review、100 次 lexical/hybrid/Capsule、cold integrity、
独立 cold Golden CLI、no-answer、provenance、source update、lineage、forgetting 和最终 integrity；
不是外推。语料是开发团队生成的 exact-token synthetic corpus，工作树 dirty，因此都固定
`claim_eligible=false`。

| 指标 | 100,000 Assets | 1,000,000 Assets |
| --- | ---: | ---: |
| Source 数 | 1 | 10 |
| 构建 | 88.76 s | 6,003.04 s |
| cold integrity | 16.69 s | 384.71 s |
| cold Golden CLI | 17.28 s | 425.35 s |
| warm lexical p50 / p95 | 0.34 / 0.56 ms | 2.27 / 3.97 ms |
| warm hybrid p50 / p95 | 3.03 / 3.51 ms | 27.73 / 30.05 ms |
| recall + Context p50 / p95 | 3.35 / 3.99 ms | 28.61 / 30.91 ms |
| peak RSS | 1,548,713,984 B | 4,857,741,312 B |
| SQLite | 739,250,176 B | 7,380,508,672 B |
| Hit@1 / Capsule / provenance | 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 |
| latency / lifecycle / integrity gates | passed | passed |

报告：[`100k`](../benchmarks/scale/retrieval-fabric-100k-2026-07-27.json) 与
[`1m`](../benchmarks/scale/retrieval-fabric-1m-2026-07-28.json)。正式 100k 支持仍需 clean frozen
candidate 重跑；1m 冷启动和更新耗时是明确弱项，不应只展示 warm latency。

## 22. 每个具名基线的逐项结果

闭合 registry SHA-256：`13e94e7350c3a8fc1572d00b079afc6c2d1b39cf93f7c6c992be44707d59720b`。
当前没有用简陋自写实现冒充第三方项目；因此没有运行就如实记录为 `pending_execution`。

| # | System ID | 配置 | 固定 revision / release | 结果 |
| ---: | --- | --- | --- | --- |
| 1 | `baseline/bm25` | Pyserini BM25 | `8f6964c95d01` / reviewed HEAD 2026-07-27 | `pending_execution` |
| 2 | `baseline/dense` | Pyserini dense retrieval | `8f6964c95d01` / reviewed HEAD 2026-07-27 | `pending_execution` |
| 3 | `baseline/bm25-dense-reranker` | Pyserini BM25 + dense + reranker | `8f6964c95d01` / reviewed HEAD 2026-07-27 | `pending_execution` |
| 4 | `ragflow` | RAGFlow | `76acd499d4fc` / v0.26.4 | `pending_execution` |
| 5 | `microsoft-graphrag` | Microsoft GraphRAG | `14a00ad88fc3` / v3.1.1 | `pending_execution` |
| 6 | `lightrag` | LightRAG | `bbebdd64272d` / v1.5.5rc1 | `pending_execution` |
| 7 | `graphiti` | Graphiti | `9140123a7282` / v0.29.2 | `pending_execution` |
| 8 | `mem0` | Mem0 | `b357a5a1b03c` / Python v2.0.14 | `pending_execution` |
| 9 | `cognee` | Cognee | `325acf356a81` / v1.4.0.dev0 | `pending_execution` |
| 10 | `memos` | MemOS | `344cab73c2d0` / local plugin v2.0.11 | `pending_execution` |
| 11 | `pageindex` | PageIndex | `39121c4d3479` / v0.3.0.dev3 | `pending_execution` |
| 12 | `openkb` | OpenKB | `ff54396e575e` / v0.4.5 | `pending_execution` |
| 13 | `wikigraph` | WikiGraph | `8dc2b2e0642b` / reviewed HEAD 2026-07-27 | `pending_execution` |
| 14 | `obsidian-native` | Obsidian native workflow | `8173c5931a6f` / Desktop v1.12.7 | `pending_execution` |
| 15 | `deeplaw/lexical` | DeepLaw lexical | final candidate commit required | `pending_execution` |
| 16 | `deeplaw/hybrid` | DeepLaw hybrid | final candidate commit required | `pending_execution` |
| 17 | `deeplaw/full` | DeepLaw full | final candidate commit required | `pending_execution` |

逐题 raw output、失败、build/query cost、paired bootstrap、confidence interval 和
Holm–Bonferroni 尚不存在，因而超越门禁没有结果。

本仓提供的 execution-plan/receipt v2 只用于让独立执行可审计：它绑定并在启动前复核 registry
exact bytes/canonical hash、clean Git root/revision/submodule inventory、corpus/query bytes、query
case-ID inventory、wrapper/executable，以及固定 hardware/software/models/common reader/network/
measurement 的 environment record。互不重叠的五个新路径保留 raw output、resource record、stdout、
stderr 和 receipt；resource record 必须绑定 build/query time、peak memory、index/workspace bytes、
model calls/tokens/cost 与 closed failure inventory。非零退出、timeout/output limit 的有界日志会保留；
零退出仍须分别通过 closed JSONL/case coverage 与资源记录校验。

collection gate 会重新打开所有 plan、receipt、clean checkout、冻结输入和 artifact bytes，检测
post-receipt drift，并要求 17 项系统使用同 evaluator run、corpus、queries、case inventory、hardware、
reader、measurement、Token budget 与 top-k，同时保留 raw output/resource/failure inventory。外部
registry 可以在不改正式包版本的前提下把 DeepLaw 三个 profile 绑定到 exact final candidate commit，
但仍保持 `pending_execution` 和 `claim_eligible=false`。runner 不冒充 OS 网络 sandbox，断网必须由
evaluator 外部实施；plan/receipt/collection digest 只有内容完整性，不是独立签名或领先证据。

最终 External Evaluator Kit freezer/verifier 已实现，但当前不会也不能生成“完成”产物。freeze 必须
同时取得 clean exact commit、冻结 registry、17 项全部成功 collection、预交付 corpus commitment、
完整模型文件 manifests、10,000 次 paired bootstrap/95% CI/Holm–Bonferroni 的 passed internal gate、
逐题/比较 manifests、reproducible wheel/sdist、OCI container、SBOM、lock、contracts、profiles、raw
outputs/resources/manual captures 与签名工具。输出为 content-addressed portable directory；verify 会
复核 source archive、全部 blob 和跨 artifact 语义绑定。detached Ed25519 只在调用方提供独立 trusted
key 时验签，且仍明确 `organization_identity_independently_verified=false`、`claim_eligible=false`。

## 23. DeepLaw 仍然落后的指标和能力

1. **竞争指标未知**：Task Success、Useful Context Recall、Irrelevant Context Rate、No-answer
   error、Token efficiency 和各专业基线非劣性都没有同协议外部结果。
2. **Dense 默认未过门禁**：公开开发消融提高 Recall@5，但 irrelevant rate 从 `0.3342` 上升至
   `0.6794`，所以默认激活被拒绝。
3. **自然语言长尾未冻结**：当前 typo 只覆盖 lexical miss 后的有界 ASCII 单编辑；中文 typo、
   proximity、完整繁简/同义表达、偏好，以及多实体/两跳/global synthesis 的任务成功仍需独立
   held-out；exact-token scale 不能替代质量测试。
4. **复杂文档解析仍未冻结**：Python AST、JavaScript/JSX、TypeScript/TSX、Java、Go、Rust 的
   pinned Tree-sitter AST 和 SQLGlot SQL AST 已闭合本地 contract；PDF 实机诊断仍未覆盖
   OCR/table/figure/damaged/multilingual，超界代码只能显式降级到 bounded lexical fallback。
5. **百万级冷路径昂贵**：cold CLI `425.35 s`，update/review `572.93 s`，peak RSS `4.86 GB`，
   SQLite `7.38 GB`；warm 快不等于整体低成本。
6. **平台和人机证据不足**：Windows ACL、Linux/macOS/Windows fresh install、真实 TUI usability、
   Codex 模型/session 矩阵与 Claude Code/OpenCode host matrix 尚未返回；Codex 目前只完成本地
   plugin lifecycle smoke。
7. **发布身份未完成**：`.dlk` 仍只有内容完整性；本地 Review Receipt 未签名；最终 release
   signature/attestation 仅有 workflow，尚无 tagged-run artifact。
8. **连接器真实环境边界**：一次性 HTTPS / local exact-Git Snapshot 已实现；真实公共 HTTPS
   endpoint/certificate/redirect、三 OS Git 与 adversarial server matrix 未冻结。认证 URL、私网、
   代理 fallback、远程 clone、后台轮询/同步不在当前支持面。

## 24. 修复和复测

本轮主要发现与修复：

- 1m 诊断暴露 exact semantic lookup 对整个 Asset table 扫描；改为 active indexed equality，
  explicit phrase 改为 FTS-bounded candidate 后再确认 substring。当前 Retrieval Fabric v2 重跑的
  1m warm lexical p95 为 `3.56 ms`，完整工作负载通过。
- Source IR root 定位由逐 block 线性扫描改为 interval index；Identity integrity replay 预加载
  fragment/node binding，避免大规模随机查询。
- graph/temporal 关系入口补齐 evidence source active/reviewed/restricted/lifecycle 中央门禁；
  source-free legacy edge 不再进入当前 Fabric/MCP。
- Query Plan hash 先绑定 admission filter/reranker；trace verification 能检测 plan tamper。
- source update/remove 补齐 source governance、Asset governance、deleted lineage 与历史保留。
- relation update 补齐 source successor evidence 重绑定、unchanged carry-forward、modified full-review、
  deleted/ambiguous fail-closed 和无 ID Golden/Workbench 二次审核；没有任何 approval inheritance。
- split/merged/ambiguous 新增 exact source-bound cross-key 人工 mapping、幂等 replay、Workbench 可见行
  选择、relation blocking 和 Identity snapshot tamper detection；mapping 本身不改变 lifecycle。
- Retrieval Fabric v2 新增 lexical-miss-only ASCII 单编辑修复、distant-noise no-answer、source-bound
  reviewed 两跳 traversal 与跨来源 global Capsule verification。
- source alias `--latest` 改为选择唯一 pending successor 或 active revision；并行 pending 分支失败
  关闭，不再依赖秒级时间戳与 source ID 排序。
- 新增显式 HTTPS / local exact-Git owner-only Source Snapshot、ingest job v2、v1 forward
  normalization、Golden 无 ID 接入、DNS-pinned TLS / Git protocol-off 边界与篡改回归；两类来源
  都保持 review-gated，HTTPS 强制 untrusted。
- Golden Path 新增十种 Markdown/HTML/JSON/JSONL/YAML/TOML/CSV/TSV/SQL/Python 混合目录的真实
  `add → review → recall` 回归；Source CLI 新增稳定 alias、active/latest 与 rename 后历史 alias 回归。
- SQL 改用 exact-pinned SQLGlot AST；OOXML/EPUB 全包 inventory 与 relationship target 失败关闭，
  PPTX 按 presentation/object/notes relationship 顺序提取，DOCX v3 保留 list/endnote 并在读取
  document.xml 前拒绝未使用的危险成员。
- Source Adapter v4 增加 XML byte/node/depth、XLSX cell/shared-string/row/merged-range 闭合门禁；
  SQLGlot recursion exhaustion 进入显式 bounded lexical fallback。
- Workbench approve/reject batch 改为单事务并增加 quarantined proposal 独立风险确认；回归证明
  batch 中后项失败不会留下前项 decision。
- Retrieval Fabric 的 integrity gap 不再只检查 exclusion reason 第一项；restricted 等前置原因不会
  掩盖 stored-source tamper/missing 诊断。
- 100k 与实际 1m 完整 scale workload 均在最终 scale-bound implementation hash 上重跑；两份报告的
  latency、lifecycle、no-answer、provenance、cold CLI、Capsule 和 integrity gate 全部通过，仍因
  dirty synthetic corpus 固定 `claim_eligible=false`。
- corruption matrix 新增 Source IR tree 与 reviewed graph 数据库篡改，分别由 fragment digest 和
  Identity snapshot mismatch 检出。
- typed extractor、reranker、document converter、dependency probe、ACL 和 official baseline wrapper
  使用 bounded subprocess，限制 stdin/stdout/stderr/time。
- official baseline wrapper 升级为 closed execution-plan/receipt v2：执行前重新加载 exact registry，
  复核 clean checkout/submodule、corpus/query/case inventory、wrapper/executable 和固定 environment；
  resource/failure record、共同 reader 与 collection fairness gate 已纳入，失败日志落盘，缺失/额外
  case、非有限 latency、资源绑定错误、artifact drift 和重算 plan 后伪造 registry entry 均失败关闭。
- full-suite 负载下发现 document-engine output-limit event 与 timeout 的末端竞态；清理 reader 后先
  判定已经观测到的超限根因，再处理 timeout。目标用例连续 10 次通过，actual-PDF 绑定重跑成功。
- 首次全量 v0.7 测试为 `470 passed, 2 failed, 2 skipped`；两个失败都是旧测试仍假设旧 subprocess
  monkeypatch 与历史 benchmark 绑定当前 source hash，已修正为新边界和历史 immutable hash。

在全量负载下，极慢的子进程启动曾让 output-limit 用例先命中 3 秒 timeout；产品仍按 timeout
失败关闭，测试启动余量调至 10 秒后重复单测与全量回归均稳定通过。

最终本地复测（2026-07-28）：`uv lock --check`、`uv run --frozen ruff check .`、
`git diff --check` 均通过；`uv run --frozen pytest` 为 **585 passed, 3 skipped in 66.56s**。两个
skip 是只能在原生 Windows 执行的 ACL/junction 用例；另一个是本机没有 historical manifest
固定 SHA-256 的 exact v0.6 wheel（自包含 v0.6 migration fixture 已通过）。任何 skip 都不被表述为通过。

## 25. P0 / P1 / P2

| 优先级 | 状态 | 内容 |
| --- | --- | --- |
| P0 | 本地实现与回归完成 | Identity v2、additive migration/rollback、many-to-many ref graph、Knowledge Lineage、temporal/evidence-governed relation 与显式 relation carry-forward review |
| P1 | 候选实现完成，发布证据未闭合 | Source IR/Tree、compiler modes、Retrieval Fabric/Duties/Capsule、Inbox/Profile、Golden CLI/Workbench、稳定 source alias、projection/Skill、reliability、Codex plugin-lifecycle smoke、固定环境/resource/manual baseline evidence 与 17-system collection gate、release tooling、100k/1m construction diagnostic |
| P1 blocker | External verification pending | clean final freeze、official baselines/statistics、Windows/OS/host matrix、secret held-out、independent signatures、tagged signing/attestation |
| P2 | Planned / external matrix | HTTPS/Git connector 公网与三 OS adversarial matrix、adversarial full-format matrix、完整自然语言 suites、TUI accessibility/usability、`.dlk` publisher signing |

## 26. Release artifact 与 hash

最终本地构建记录（2026-07-28）：

- default / build / Discovery / document-engine dependency audit 分别检查 41 / 56 / 57 / 128 个
  package，均无
  known vulnerability 或 adverse status；document-engine 的 4 个 advisory 仅由 exact OpenVEX
  `not_affected` 路径覆盖；
- 两次固定 `SOURCE_DATE_EPOCH=946684800` 构建逐字节一致，package inventory verified；
- PEP 517 backend 固定为 `hatchling==1.31.0`，完整 isolated-build dependency set 由
  `benchmarks/release/build-constraints.txt` 精确约束、进入 lock/build audit，并由 report hash 绑定；
- release workflow 直接把这两次比较后保留的 exact verified bytes 交给 fresh-wheel、签名与
  provenance/SBOM attestation，不再另行构建一组未与 report digest 对照的 distribution；
- reproducible wheel：`deeplaw-0.6.0-py3-none-any.whl`，511,088 bytes，137 paths，SHA-256
  `d0e2a825ee4cf61af09b30a2508f311eea5f80ca94b8ff5009fc4d08536832a4`；exact verified bytes
  已保留在 ignored `dist/verified-handoff-codex-lifecycle-v4/`，其 fresh-wheel lifecycle 通过；
- reproducible sdist：`deeplaw-0.6.0.tar.gz`，8,072,206 bytes，329 paths，SHA-256
  `00995f29adef5d75c1b4499188a0586d3f938097cd61003cbbb2ce8f54d60c56`；
- reproducibility report SHA-256
  `3145800fef24a046a3b01af647200fa559ffd5cdded92080cf779efb22253fef`，其
  `release_claim_eligible=false`、blocker 为 `working_tree_not_frozen`；
- exact verified wheel 的 fresh-wheel
  `init → source add → review → HTTPS zero-network preflight → context → verify-capsule` 全部有效；
- CycloneDX 1.5 SBOM 含 128 components，SHA-256
  `bd55c2ec9a7c676f5e3fc67a1971e5cf8d27ef42cee8c4958d7d1da2c976cd67`；
- installed-license inventory 检查 115 packages，`blocked=[]`、`review_required=[]`，SHA-256
  `91bf622153d0daab32dd543a46902eb97b3d799391f0871877fe51153511c3fb`。

verified artifacts 与 construction evidence 保存在本机 ignored `dist/`；它们不是 tagged release
附件，也不关闭平台、签名或外部评测门禁。

这些是 dirty-worktree construction artifacts，不是签名 release。正式发布必须从 clean final commit
重新构建，并把 commit/source tree/lock/contracts/models/tokenizers/profiles/corpus/baseline/raw
outputs/resources 一起冻结。

持久诊断 hash：

| Artifact | SHA-256 |
| --- | --- |
| `uv.lock` | `cbcf647ce0fdb1b5767c6654c7061d2044ddea322d3763071d0f45dc0a8fa8bd` |
| exact build constraints | `0f03fb3f3925513d568a106da29a75e843a9a65923bdab7fad392cf22f0cdd1c` |
| baseline registry | `13e94e7350c3a8fc1572d00b079afc6c2d1b39cf93f7c6c992be44707d59720b` |
| Retrieval Fabric 100k report | `556fd25082c8dbc76355ccbb22a38b2ea7f570c56d45c91c4a95738097f58380` |
| Retrieval Fabric 1m report | `4c6333c2cef0181aa63dc4ddaaaaa9429b617b9672391595631fef2aa9324e8c` |
| actual-PDF report | `6e391852291309d88779c486544d2312a6410cbfc8efcf1767467b77207c4bf2` |
| Typed Compiler dev scorer report | `06aaf24420f4db1c86da620cd92cb0f576a250f43c262ad5715a3d9ac1c3090e` |
| Codex plugin-lifecycle smoke report | `70b2c794f604d3d09ced4bc04d949c96698b9eb684a7070456e916780a4fb030` |

## 27. 外部评测状态

| 门禁 | 当前状态 |
| --- | --- |
| 17 项 named baseline 同协议运行 | `pending_execution` |
| 每个专业基线 ≤1pp 非劣 + 至少一项胜出 | 未执行 |
| 综合 Macro Task Success / Recall / Noise / Provenance / Token | 未执行 |
| paired bootstrap / CI / Holm–Bonferroni / failure disclosure | 未执行 |
| Windows native ACL 与三 OS clean install | External verification pending |
| Codex local plugin marketplace/install/remove/readd lifecycle | passed on one real local Codex CLI；lifecycle-only，`claim_eligible=false` |
| Codex model/session + Claude Code / OpenCode real-host E2E | External verification pending |
| 两个秘密 held-out | 未提供给开发团队，符合协议 |
| 两家真实独立 evaluator attestation | 未执行 |
| final tag Sigstore + GitHub provenance/SBOM attestation | workflow ready，tagged run pending |

在这些结果齐备前，claim gate 应继续失败或返回 pending；不能用内部 synthetic、公开开发集、
confidence score 或自签名替代独立证据。

## 28. 当前可以和不能公开宣称的内容

可以公开宣称：

- DeepLaw 的产品边界是本地单用户、Agent-first、来源可验证、人工治理的 Knowledge OS；
- 当前 `0.7.0-unreleased` 工作树已实现本报告列出的候选工程能力；
- Agent MCP 保持只读，默认无遥测、无远程数据库、无自动 active memory；
- 当前机器上的 100k/1m exact-token construction diagnostic 及其完整资源数字；
- dependency/OpenVEX、license/SBOM/reproducibility/fresh-wheel 工具和本地运行结果；
- 单个真实 Codex CLI 的本地 plugin lifecycle smoke 已通过，但不含模型/session E2E；
- 工程目标是在冻结、公平、独立的基准中取得本地 Agent Knowledge 场景整体最优。

不能公开宣称：

- `v0.7.0` 已正式发布或当前工作树是签名商业 release；
- best、SOTA、world strongest、超过全部基线、整体最优已经成立；
- synthetic exact-token scale 等于自然语言质量或第三方复现；
- Windows、Linux/macOS/Windows、Codex/Claude Code/OpenCode 已全部实机通过；
- Dense、graph、reranker、Wiki、confidence 或 temporal match 代表 authority/approval/applicability；
- `.dlk` 已有 publisher identity，或本地 receipt 是独立数字签名；
- 秘密 held-out、独立机构、签名和外部结果已经存在。

## 交付决定

本地工程施工可进入 **candidate freeze preparation**，但不能进入正式版本升级或市场领先声明。
下一次状态跃迁只能由 clean final commit 重跑、真实平台/宿主证据和独立具名基线评测触发；文档
或版本字符串不能替代这些门禁。
