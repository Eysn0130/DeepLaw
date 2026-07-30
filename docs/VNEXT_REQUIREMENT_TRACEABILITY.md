# DeepLaw v0.10 总纲逐项落地核对

状态：**v0.10.0 release audit，2026-07-30**。本表以用户提供的最新《一、DeepLaw
项目总纲》及其“十九、终局实施顺序”为基准，代码事实以 `src/deeplaw`、`contracts`、迁移、测试和
`uv.lock` 为准。DeepLaw 2.0 是产品品牌，不是软件版本；`v0.10.0` 在 0.8/0.9 工程交付之上完成
1.0 自主可验证质量闭环。质量协议通过不等于取得跨产品竞争领先证明。

状态含义：

- **已实现**：存在运行时代码、闭合 contract、迁移/恢复或测试证据。
- **兼容实现**：由 v0.7 已有正确能力继续提供，并被新内核纳入验证/分区。
- **比较待执行**：需要真实宿主或竞争系统的同条件结果；仓库只实现协议和门禁，不能伪造结果。

## 1. DeepLaw 0.8：Autonomous Knowledge Core

| 总纲要求 | 状态 | 落地位置与闭环 |
| --- | --- | --- |
| Markdown Knowledge Revision | 已实现 | `knowledge_autonomy.py`；`knowledge-object.v2`、`knowledge-revision.v2`；稳定 ID、受约束 YAML、Markdown hash、CAS object、parent/supersedes、双时态与 workspace materialization |
| Knowledge Commit Coordinator | 已实现 | 单一 `AutonomousKnowledgeStore` 写入服务；CAS → `BEGIN IMMEDIATE` Ledger/event/idempotency/pending → 原子工作副本；startup `recover()`；失败不切 current pointer |
| `knowledge_sink` | 已实现 | 独立进程/leaf、v2 closed input/output、owner token、writer、scope/sensitivity、operation、bytes/rate/capacity、幂等与撤销；不含 Legal/source/authority/admin 写入 |
| Agent Memory 自动生效 | 已实现 | admitted Agent revision 直接 `active`；未知来源、权限提升、注入、无效 Run 等进入 `quarantined`；无需逐条人工 Review |
| Authority 与 Epistemic State | 已实现 | origin/authority/verification/lifecycle/scope/sensitivity/provenance/temporal 独立字段；`supported/tentative/contested` 不改变 authority；ranking 永不升级权限 |
| Claim、Concept、Entity、Event | 已实现 | typed Sink operations；Claim 强制 Source/Run binding；另含 Decision/Procedure/Experience/Preference/Comparison/Synthesis/Memory/Skill；对象、revision、alias、关系和事件逐项校验 |
| Watcher | 已实现 | 显式前台 bounded polling Watcher；只调用同一 reconcile/coordinator；不是后台守护进程或第二套写逻辑 |
| 普通知识取消统一人工 Review | 已实现 | 新自主平面按 scope-bound policy 自动 active；v0.7 proposal/review 仅保留为 source-derived 兼容/来源准入路径 |
| Legal Pack 自动更新协议 | 兼容实现 | exact-byte Ed25519 先验签再解析/下载、trust root/revocation、catalog identity、单调 sequence、防回滚、immutable release、失败关闭与原子 active pointer；网络禁用 unsigned bypass |

0.8 的扩展闭环还包括：不可变 Run Record、32 项 bounded capture、精确重复折叠 receipt、语义候选、
高精度矛盾并存、Concept/Entity alias lookup、same-as/merge/split 决议、Wikilink 编译、文件 lease、
compare-and-swap、显式 conflict、TTL、forget、owner-only content GC、snapshot/restore 和 v0.7 rollback。

## 2. DeepLaw 0.9：Living Wiki and Knowledge Intelligence

| 总纲要求 | 状态 | 落地位置与闭环 |
| --- | --- | --- |
| Living Overview | 已实现 | `rebuild_derived()` 生成 hash-bound `wiki/index.md`、`wiki/overview.md`；明确 `derived_view` 和 `authority:none` |
| Entity/Concept Page | 已实现 | 生成 Concept/Entity/Event/Comparison/Synthesis typed pages，回链 canonical Markdown revision 与 relations |
| Synthesis | 已实现 | 独立 `save_synthesis` capability 与 kind，不可通过普通 remember 偷渡 |
| Semantic Lint | 已实现 | source-free/inactive provenance、duplicate key/content、broken/ambiguous link、未编译 relation hint、orphan、contested-without-counterevidence、alias ambiguity；按 scope/sensitivity 准入并对 object/relation/link scan 与输出分别设硬界限 |
| Community Detection | 已实现 | deterministic weighted label propagation + semantic bridge；community view 可删除重建，不写 authority |
| Gap Discovery | 已实现 | read-only `gaps`/`discover_gaps()`，输出 missing evidence/orphan/conflict/link/provenance gaps 与 counts；CLI/MCP 都绑定 exact scope/max-sensitivity，不能泄漏其他分区 ID 或计数 |
| Memory Consolidation | 已实现 | 多输入 memory → 新 summary revision + `consolidates` relations + input archived revisions + consolidation event；确定性 operation digest 与 crash-safe retry |
| Obsidian/Tolaria 完整互操作 | 已实现 | files-first Markdown/YAML/Wikilink、稳定 ID、rename/move、external edit reconcile、冲突保留、JSON Canvas、Git-friendly views；不使用 CRDT/LWW |
| Skill Factory | 已实现 | 显式可检查 Procedure step → governed draft Skill；无可检查 criterion 则 abstain；不执行、不授予 capability；promotion 需 user/external helpful eval |

附加的知识智能闭环：默认离线 multilingual hash-dense index、evidence-duty reranker、exact/lexical/
dense/graph/temporal/memory/Wiki/source-derived Source Tree/code symbol 通道、item/character/token/source/hop/
provider payload 预算、current/historical admission、Query Plan/selection digest、Living Wiki/Canvas/Vector
manifest 全量 hash 绑定和 stale-index fail closed。

## 3. DeepLaw 1.0：Quality and Superiority Closure

| 总纲要求 | 状态 | 当前事实与不可伪造边界 |
| --- | --- | --- |
| 公开 Benchmark 与固定评分 | 已实现 | `benchmarks/evaluation/protocol-v1.json` 固定四组件、权重、分项/总分阈值、hard failures、离线策略和宣称边界；JSON Schema 与测试拒绝口径漂移 |
| 时间冻结 holdout | 已实现 | `repository-temporal-holdout-v1.json` 绑定真实仓库来源 bytes/anchor/hash，覆盖中文、英文、代码、法律和长文档；候选必须严格晚于最后 freeze commit。数据公开且维护者可见，明确 `secret=false`、不声称 contamination-free |
| 自动化报告与验证 | 已实现 | `run_protocol.py` 生成 summary、四份完整组件报告、Markdown 报告、functional scoring digest 和 SHA256SUMS；verify 模式检查 schema、内部 digest、组件 bytes 与完整 checksum inventory |
| 发布制品绑定 | 已实现 | 正式门禁在隔离环境安装 exact wheel，要求 clean worktree、freeze ancestor、candidate commit/tree/version/wheel SHA 全一致，并把报告纳入 release manifest、checksums、Sigstore 与 provenance |
| 默认本地 Dense/Reranker | 已实现 | `knowledge_intelligence.py`；offline、固定 identity/revision/dimension/quantization、audit/input/bytes manifest；损坏/过期拒绝，Authority 不受分数影响 |
| Typed Compiler 质量门禁 | 已实现并实际运行 | v1 gold suite 调用 shipped `deterministic-v2` 处理 source-bound bilingual input，按 precision/recall/F1/source span/hallucination/support/duplicate 计分；不冒充模型跨文档 synthesis |
| 自主写入与安全质量 | 已实现并实际运行 | 12 个实际 domain-service case 覆盖授权写入、幂等、CJK、stale CAS、grant/scope/revoke、authority elevation、stored injection、restricted disclosure、forget 与 Ledger hash-chain；所有安全 case 为硬门禁 |
| Codex、Claude Code、OpenCode 真实任务 | 比较待执行 | 三宿主 adapter 和 no-model lifecycle gate 已实现；当前本地仅有 Codex CLI，不能把静态配置或生命周期检查冒充三宿主真实模型任务 |
| 与列明系统公平比较 | 比较待执行 | 17-system registry、official/manual adapters、环境/plan/receipt/resource/raw-output/collection contracts 已实现；actual third-party results 仍未执行，需 paired CI 与完整成本/失败清单 |
| 外部机构认证 | 正确清退 | 不是 DeepLaw 质量或发布门禁。独立复现可选；签名只绑定 bytes/key，不自动证明机构独立、结果正确或产品 Authority |

因此，1.0 的**自主可验证质量工程面已闭环**：exact release wheel 可以取得
`quality_protocol_eligible=true`。竞争比较的实际事实仍未发生，所以
`competitive_claim_eligible=false` 保持 fail closed。

## 4. 最终架构逐项核对

| 架构要求 | 状态 | 实现/约束 |
| --- | --- | --- |
| 双知识平面 | 已实现 | immutable evidence 与 autonomous Agent knowledge 分离；Ledger/index 不形成第三 Authority |
| 不可变对象仓库 | 已实现 | `.deeplaw/objects/sha256` exact bytes；role 独立；source、knowledge 同 bytes 不合并身份；revision 不原地覆盖 |
| Markdown-native，不是 Markdown-only | 已实现 | knowledge/memory/skills 为 canonical open content；Ledger 为治理；原格式和 evidence bytes 保留 |
| `.deeplaw/ledger.sqlite3` | 已实现 | legacy root DB 原子迁移；STRICT/FK/CHECK/UNIQUE、WAL、FULL sync、`BEGIN IMMEDIATE`、integrity/replay/snapshot |
| 可重建派生层 | 已实现 | FTS/vector/graph/community/Wiki/lint/gap/Canvas/cache 由 canonical planes 重建；manifest 绑定 bytes/config/generator/audit heads |
| Knowledge Revision 双侧绑定 | 已实现 | exact Markdown hash + immutable object + Ledger governance + event；workspace mismatch 必须 reconcile/quarantine/restore/conflict |
| 规范关系与 Wikilink | 已实现 | stable endpoints/predicate/evidence/valid/transaction/writer revision 存 Ledger；Wikilink/Canvas 仅开放视图 |
| 不采用完全 Event Sourcing | 已实现 | current state tables + append-only hash-chain event；current pointer 快速查询，event 用于审计/重放 |
| 不采用 CRDT 核心 | 已实现 | single writer、file lease、base revision、CAS、explicit conflict；无字符级自动事实合并 |
| Capture→Classify→Bind→Reconcile→Commit→Connect→Learn→Forget | 已实现 | closed Run/capture、governance gate、source/run binding、coordinator、relations/index/wiki、feedback、TTL/consolidate/forget/GC |
| Authority 独立维度 | 已实现 | origin、verification、lifecycle、scope/sensitivity、`revision_only` mutability、writer scope、固定 activation-policy ID、provenance、valid/transaction time 不压成总分；source-free 不能自报 `supported` |
| Living Wiki 两类页面 | 已实现 | canonical typed Knowledge Objects 与 rebuildable overview/backlink/community/gap/Canvas 分开；derived 页面不能覆盖来源 |
| 统一 Retrieval Fabric | 已实现 | discovery/admission/selection/authority/adjudication 分离；多通道、冲突、gaps、硬预算、hashable/replayable plans |
| Knowledge Capsule 分区 | 已实现 | official/user-private/source-derived/agent-derived/memory/contradictions/limitations/gaps/receipts；Knowledge 与 Legal 查询权限分离 |
| 双层 Legal Pack | 已实现 | official signed release 与 owner-only user-private snapshot 物理独立；private 无法继承 official；add/delete 不影响 official |
| 联合法律上下文 | 已实现 | `law_support` v3 `federated_context` 独立 admission/receipt/status/authority；Agent interpretation 需显式启用与 tag admission，始终 `legal_authority=false` |
| CLI/MCP/Watcher 单 Service Layer | 已实现 | 三个入口调用同一 domain store/coordinator；`knowledge_support`/`law_support` 永久只读，Sink 单独授权 |
| Git/Obsidian/Tolaria/Canvas | 已实现 | Git 跟踪开放 workspace；正式 backup 含 CAS+Ledger+Markdown+manifest；Canvas 是派生交换视图 |
| 安全与隐私 | 已实现 | untrusted import/injection lint、scope/sensitivity、restricted suppression、provider path/secret/unicode gate、case-data confirmation、bounded parser/archive/network/process |
| Source Tree/多格式/code symbol | 兼容实现 | v0.7 Source IR/Tree 保留 order/locator/hash；Python AST、固定 Tree-sitter 与 SQLGlot adapter；在 source-derived partition 联合查询 |
| Official update 信任 | 兼容实现 | exact-byte Ed25519、revocation、catalog ID/sequence、rollback guard、atomic switch、历史 release pin；TUF 四角色是建议演进而非伪造完成项 |

## 5. 本次发现并修正的总纲风险

1. **质量闭环不应依赖外部机构。** 旧路线把 secret held-out 和两家机构签名设为发布前提，既不可
   自主复现，也会混淆签名与正确性。现由公开 Benchmark、固定评分、时间冻结 holdout、自动报告和
   exact-wheel 门禁取代；公开标签不冒充秘密数据。真实三宿主和竞争系统仍由
   `competitive_claim_eligible=false` 约束，不能生成假报告。
2. **Dense 的名称不能冒充神经模型。** 当前默认是确定性、离线、multilingual hash-dense；它有
   exact model identity 和公平评测入口，但文档不宣称等同某个外部 embedding checkpoint。
3. **Contract 不能原地扩写旧版本。** `knowledge_support` 和 `law_support` 的 v2 文件保持冻结；新增
   operation/budget 使用 v3，避免已发布宿主看到同 `$id` 不同语义。
4. **自主激活不能删除安全门禁。** 普通知识取消统一 Review，但 authority elevation、unknown
   provenance、prompt injection、scope/sensitivity 越权、case data 和非法 Run 仍 fail closed/quarantine。
5. **遗忘与字节擦除必须分开。** `forget` 是 lifecycle revision；owner GC 仅擦除所有引用都符合
   policy 且没有 evidence role 的 bytes，保留最小 governance/audit tombstone，并支持 crash recovery。
6. **联合检索不能先 Top-K 再做治理准入。** scope、sensitivity、lifecycle、valid time、kind 和
   `required_tags` 已下推到 FTS、canonical fallback、historical lexical、dense 与 relation candidate
   scan，并在 reranker 前统一复核；无权或无关候选不能占满 Top-K。官方为空时也不把 private/Agent
   结果改名。
7. **Gap/Lint 也是读边界。** 旧实现的全 Vault Lint 会让低权限 `gaps` 暴露 restricted/其他 scope
   的 ID 和聚合计数；现已在 SQL、relation 与 alias 三层按调用边界过滤，并增加回归测试。
8. **派生索引不能绑架规范提交。** 旧实现每次 remember/relation/consolidate 后同步全量 rebuild，
   与总纲异步派生层冲突且会造成写放大；现改为事务内排队、Watcher/显式 rebuild 消费，失败保留
   pending，读取拒绝 stale manifest 并使用有界 canonical fallback。
9. **关系没有 Evidence 就不是可准入事实。** 新 `add_relation` contract 强制至少一个可绑定
   evidence reference；source-free 历史行仅保留审计，不进入图遍历、冲突挑战或 Capsule。未成功
   编译为 evidence-bound canonical relation 的 Markdown relation hint 会进入 Gap，而不是静默当作边。
10. **有界返回不等于有界执行。** identity、graph、contradiction 和 Semantic Lint 的候选扫描均有
    明确硬上限和 truncation/gap；exact identity ambiguity 不会因返回 limit=1 被误报为 resolved。

## 6. 交付门禁

仓库实现必须通过：

```bash
uv run pytest
uv run ruff check .
git diff --check
```

正式质量发布还必须从 clean candidate 的 exact wheel 运行
`benchmarks.evaluation.run_protocol --require-eligible` 并验证完整报告目录。竞争声明另须通过
`docs/EXTERNAL_BENCHMARK_PROTOCOL.md`、实际具名系统与三宿主任务、paired statistics 和完整
resource/failure inventory；独立复现可选，不是认证门槛。任何比较证据缺失都必须保持
`competitive_claim_eligible=false`。
