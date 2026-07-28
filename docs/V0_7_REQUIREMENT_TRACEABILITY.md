# DeepLaw v0.7 需求—证据追踪矩阵（GA 前历史记录）

> 本文保留施工阶段的未冻结状态；正式商业 GA 门禁见
> `docs/V0_7_ACCEPTANCE_MATRIX.md` 和发布资产中的 commercial manifest。

审计日期：2026-07-28（Asia/Shanghai）
候选状态：`0.7.0-unreleased`，工作树未冻结
规范来源：用户给定的 36 项最终端到端验收、测试矩阵和发布门禁

本文件把“已实现”“本机已验证”和“正式发布已通过”分开，避免用代码存在、synthetic
diagnostic 或 skipped test 代替真实发布证据。

状态定义：

- **Local passed**：当前本机有可重复测试或机器报告；不自动代表跨平台或外部验证。
- **Partial**：主路径存在，但需求中的质量、格式、平台或真实环境证据未闭合。
- **External pending**：只能由冻结候选、目标平台、真实宿主、具名基线或独立评测方完成。

## 36 项最终端到端验收

| # | 验收项 | 当前状态 | 可复核证据 | 未关闭门禁 |
| ---: | --- | --- | --- | --- |
| 1 | fresh install | Partial | `tests/test_release_engineering.py` 的 fresh-wheel lifecycle；`benchmarks/release/verify_fresh_wheel.py` | Linux/macOS/Windows 冻结 wheel 实机矩阵 |
| 2 | init | Local passed | `tests/test_golden_cli.py::test_five_command_golden_path_requires_no_internal_ids` | 冻结 release 重跑 |
| 3 | add mixed-format directory / explicit connector snapshot | Local passed | mixed-format Golden E2E；真实 local exact-Git Golden E2E；HTTPS network-free preflight 与 snapshot/job contract tests；`tests/test_source_adapters.py`、`tests/test_source_connectors.py` | heavy formats 跨平台/对抗样本；真实公共 HTTPS endpoint/certificate/redirect 冻结矩阵 |
| 4 | progress/resume | Local passed | `tests/test_knowledge_jobs.py` 的 checkpoint/retry/cancel/crash-safe records；Golden `add --resume/--retry/--cancel` | 大目录真实可用性 |
| 5 | automatic Source IR | Local passed | `contracts/source-ir.v1.schema.json`；Python AST；五类 exact-pinned Tree-sitter grammar；SQLGlot AST；`tests/test_source_adapters.py`；`tests/test_v070_contracts.py` | 复杂 PDF 与跨平台 adversarial parser matrix |
| 6 | typed proposals | Partial | `tests/test_typed_extractor.py`；`tests/test_typed_compiler_benchmark.py`；`benchmarks/typed_compiler/` | 冻结 reviewed gold corpus 的质量门禁 |
| 7 | interactive review | Partial | Golden TTY 路径、`tests/test_operator_workbench.py` curses smoke、atomic batch rollback、独立 quarantine-risk confirmation 与 canonical review service | 真实终端 usability/accessibility |
| 8 | update source | Local passed | `tests/test_knowledge_control.py::test_source_update_is_review_gated_and_switches_versions_atomically` | 冻结候选重跑 |
| 9 | lineage mapping | Local passed | `contracts/knowledge-lineage-review.v1.schema.json`；`tests/test_lineage_workflow.py`；`tests/test_operator_workbench.py` 的可见行选择；Identity migration suites | 大规模 split/merge 人工 gold set |
| 10 | relation carry-forward review | Local passed | `src/deeplaw/relation_workflow.py`；`contracts/relation-carry-forward.v1.schema.json`；`tests/test_relation_workflow.py`；Golden 无 ID 双阶段 review；reviewed cross-key mapping 阻断 | split/merge 真实人工样本集 |
| 11 | build lexical/dense/tree/graph indexes | Partial | lexical FTS/rebuild、Source IR tree、SQLite adjacency、显式 Discovery Index；`tests/test_retrieval_fabric.py`、`tests/test_knowledge_discovery.py` | Dense 仍 opt-in 且未过最终质量门禁；tree/graph 为内嵌结构而非独立默认 sidecar |
| 12 | recall exact | Local passed | `tests/test_retrieval_fabric.py::test_exact_lookup_and_no_answer_are_explicit` | 外部 corpus |
| 13 | recall Chinese paraphrase | Partial | 繁简兼容、有限同义词/CJK n-gram 回归在 `tests/test_retrieval_fabric.py` | 冻结自然语言 paraphrase held-out |
| 14 | recall long-document tree | Partial | Source Tree API 与长内容预算测试在 `tests/test_source_adapters.py`、`tests/test_context_compiler.py` | 真实长 PDF/office gold set |
| 15 | recall temporal | Local passed | `tests/test_knowledge_identity_v2.py::test_temporal_relation_preserves_current_past_and_as_of_views` | 法律/事实适用性始终需人工判断 |
| 16 | recall multi-hop | Local passed | `tests/test_retrieval_fabric.py` 的 source-bound reviewed two-hop、预算与 trace 回归 | 独立多跳 gold set；当前不宣称通用 GraphRAG 等价能力 |
| 17 | global synthesis context | Local passed | `global` Query Plan/Fabric、跨来源选择、token-bounded Capsule 与 `verify_capsule` 回归 | 冻结 cross-document synthesis quality suite |
| 18 | no-answer | Local passed | explicit no-answer/gap 回归与 100k/1m diagnostic | 具名基线错误率比较 |
| 19 | explain trace | Local passed | `contracts/knowledge-retrieval-trace.v1.schema.json`；Golden acceptance；tamper tests | 真实宿主展示 |
| 20 | token-bounded Capsule | Local passed | `tests/test_context_compiler.py` 的 hard character/item/source/token/serialized bounds 与 verification | tokenizer-specific release profiles |
| 21 | Agent MCP | Partial | `tests/test_knowledge_mcp.py` 的只读、bounded、restricted、tamper、case boundary；真实 Codex CLI 双插件 marketplace/install/remove/readd 隔离生命周期与 exact cache-copy 证据在 `benchmarks/hosts/codex-plugin-smoke-2026-07-28.json` | Codex 冻结 wheel 的模型/session activation、tool discovery、recall/context/verify/negative matrix；Claude Code/OpenCode 真实宿主矩阵 |
| 22 | Run Record | Local passed | `tests/test_knowledge_control.py` Capsule-bound Run/feedback loop | 真实宿主 post-run artifact |
| 23 | structured feedback | Local passed | `tests/test_knowledge_control.py`、`tests/test_knowledge_cli.py` | 外部任务收益评测 |
| 24 | proposal inbox | Local passed | `tests/test_knowledge_inbox.py` 的隔离、hash、tamper、promote/reject | 真实宿主自动提交（仍只能写 Inbox） |
| 25 | ranking profile evaluation | Local passed | `tests/test_retrieval_profiles.py` 的 train/evaluate/gate/activate/rollback | 冻结 held-out 的正收益 |
| 26 | feedback replay | Local passed | `tests/test_knowledge_control.py` 和 profile regression | 真实长期序列 |
| 27 | selective forgetting | Local passed | `tests/test_retrieval_fabric.py` 的 current exclusion、relation invalidation、history retention | 大规模长期运行 |
| 28 | Obsidian projection | Local passed | `tests/test_knowledge_markdown.py`；deterministic manifest/backlinks/reverse quarantine | Obsidian 真实应用 smoke |
| 29 | JSON Canvas | Local passed | projection/Canvas contract tests与生成物检查 | Obsidian 真实应用 smoke |
| 30 | Skill build | Local passed | `tests/test_skill_factory.py` 的 deterministic build/verify/install/update/tamper | 三宿主真实消费 |
| 31 | snapshot/restore | Local passed | `tests/test_knowledge_maintenance.py` | 三 OS 冻结候选 |
| 32 | corruption detection | Local passed | audit/state/source/FTS/Capsule/package/snapshot/inbox/profile/tamper suites | native filesystem fault injection 扩展 |
| 33 | Windows ACL | External pending | payload evaluator tests本机通过；`tests/test_windows_acl.py` 两项 native test 在非 Windows skip；CI 有 `windows-latest` | 冻结候选真实 Windows run |
| 34 | 100k Asset | Partial | `benchmarks/scale/retrieval-fabric-100k-2026-07-27.json` 全工作负载通过 | dirty synthetic construction diagnostic；clean frozen rerun |
| 35 | competitor benchmark | External pending | 17 项闭合 registry；execution-plan/receipt v2 固定并复核 clean revision/submodule、registry/corpus/query/case inventory、wrapper/executable、hardware/software/models/common reader/network/measurement；resource record 记录 build/query/memory/disk/model cost/failures；collection gate 重新验全部输入/产物和同条件公平性；全部结果仍为 `pending_execution` | evaluator OS 断网、同协议真实运行、统计检验、秘密 held-out 和独立复核/签名 |
| 36 | reproducible release | Partial | exact constrained/audited build environment、byte-identical wheel/sdist、verified bytes 直接发布给 signing job、inventory、fresh-wheel、SBOM、license、OpenVEX；External Evaluator Kit freezer 会闭合复核 source/lock/contracts/models/profiles/OCI/raw/resource/signature-tool bindings；tag workflow 被 Linux/macOS/Windows frozen-candidate gate 阻断在前 | clean final commit、17 项真实 collection/internal gate、OCI 候选、version/tag、真实 workflow run、签名与 provenance artifact |

## 测试矩阵覆盖审计

### 来源与结构

| 需求组 | 本地证据 | 判定 |
| --- | --- | --- |
| Markdown、HTML、JSON/JSONL、YAML/TOML、CSV/TSV、SQL、Python/code | `tests/test_source_adapters.py` 与 mixed-format Golden E2E | Local passed |
| PDF、DOCX | document extraction tests；actual-PDF 诊断 `benchmarks/release/document-engine-actual-pdf-2026-07-28.json` | Partial：复杂 OCR/table/figure/damaged/multilingual 未覆盖 |
| PPTX、XLSX、EPUB | relationship-defined order、完整 archive inventory、XML byte/node/depth、XLSX cell/shared-string/merged-range 与 escaping/external target 回归；`tests/test_source_adapters.py` | Local passed；仍需真实多样样本 |
| directory、rename、move、source update | `tests/test_knowledge_control.py`、`tests/test_knowledge_jobs.py` | Local passed |
| explicit HTTPS snapshot | canonical URL、public/mixed DNS、endpoint/SNI pinning、redirect/bound/hash/type、untrusted review gate、manifest/content tamper tests | Local passed（机制）；当前网络将公网域名映射到保留测试网段并被正确拒绝，真实公网 smoke 待冻结环境 |
| local exact-Git snapshot | 真实 repo/full commit、working-tree divergence、跨 commit stable identity/pending successor、no checkout/network、object hash、origin privacy、Golden add/review/recall | Local passed；三 OS Git matrix 待执行 |
| split、merge、ambiguous | closed Lineage review contract、跨 Knowledge Key 持久化、replay/tamper、Workbench 可见行选择、relation blocking | Local passed（机制）；真实人工映射 corpus 未冻结 |
| duplicate title | `tests/test_context_compiler.py` 的跨来源/同来源 duplicate-title tests | Local passed |
| parser change | source revision/compilation/proposal identity tests | Local passed |

### 召回

| 需求组 | 本地证据 | 判定 |
| --- | --- | --- |
| exact、phrase、CJK、英文、中英混合、代码、长查询 tail entity | tokenizer/query-plan/Fabric regression | Local passed（确定性开发集） |
| 同义词、缩写、错别字、多实体 | synonym/acronym/multi-entity 回归；lexical miss 后有界 ASCII 单编辑 typo repair 与 distant-noise no-answer | Local passed（确定性开发集）；通用自然语言 held-out 未冻结 |
| tree、graph、temporal、current、as-of | Source IR、reviewed relation、temporal suites | Local passed（本地 contract） |
| 多跳、global | reviewed source-bound two-hop trace；跨来源 global Capsule/verification | Local passed（有界 contract）；任务级 gold 与具名基线未执行 |
| 偏好、流程、经验、失败复盘 | channel/duty 与结构化 feedback/profile 支持存在 | Partial；任务级 gold 与具名基线未执行 |
| contradiction、counterevidence | Context contradiction与 duty tests | Local passed（开发集） |
| no-answer、source swamp | explicit gap/no-answer、weak long-query rejection、scale diagnostic | Local passed（开发集）；比较门禁未执行 |

### 安全

| 需求组 | 本地证据 | 判定 |
| --- | --- | --- |
| stored prompt injection、invisible characters | compiler/quarantine/render-as-data tests | Local passed |
| source/database/FTS/tree/graph/Capsule/Run/Feedback tamper | integrity、MCP、Capsule、feedback、retrieval trace tests | Local passed |
| vector tamper | Discovery exact model/index binding与 tamper tests | Local passed（opt-in sidecar） |
| symlink、package escape | source/package/Capsule/snapshot path tests | Local passed |
| connector SSRF/protocol/tamper | HTTPS IP literal/private/mixed DNS 与 endpoint pinning；Git exact revision/protocol-off/lazy-fetch-off；snapshot/job revalidation | Local passed（闭合与本地 Git）；真实恶意 HTTPS server matrix 待执行 |
| junction、Windows ACL | payload tests + native Windows-only tests | External pending |
| restricted、cross-Vault、MCP write、same-owner Shell boundary | MCP/package/vault/bounded subprocess tests | Local passed；Windows native边界仍待实机 |

### 学习

| 需求组 | 本地证据 | 判定 |
| --- | --- | --- |
| automatic inbox、helpful、irrelevant、harmful、stale、missing、relation correction | Inbox/structured feedback contracts与 tests | Local passed（数据流） |
| profile training/evaluation/rollback | retrieval profile regression gate tests | Local passed（开发 suite） |
| selective forgetting、feedback replay | retrieval/feedback tests | Local passed |
| 真实长期收益 | 尚无冻结任务序列与外部宿主结果 | External pending |

## 发布阻断项

本地继续改代码不能替代下列证据：

1. 17 个具名基线在同一冻结协议、语料、预算、硬件与 evaluator 下的逐题结果；
2. paired bootstrap、置信区间、Holm–Bonferroni、失败样本与资源成本；
3. Linux/macOS/Windows fresh install、Windows native ACL/junction；
4. Codex 的冻结 wheel activation/tools/recall/context/verify/read-only/inactive-zero-impact，
   Claude Code、OpenCode 的 install 到任务全矩阵；当前 Codex 只闭合了本地 plugin lifecycle；
5. 两个秘密 held-out、两家独立机构结果与签名；
6. clean final commit 对应的正式版本、tag、release signature、SBOM/provenance attestation；
7. 正常公网环境的 HTTPS endpoint/certificate/redirect/adversarial matrix 与三 OS local-Git matrix。

因此当前可以称为“本地工程候选”，不能称为已完成外部验证的商业 release，也不能宣称 best、
SOTA、world strongest 或已经超过全部基线。
