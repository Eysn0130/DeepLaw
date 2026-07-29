# DeepLaw v0.9 总纲逐项落地核对

状态：**v0.9.0 release audit，2026-07-30**。本表以用户提供的最新《一、DeepLaw
项目总纲》及其“十九、终局实施顺序”为基准，代码事实以 `src/deeplaw`、`contracts`、迁移、测试和
`uv.lock` 为准。v0.9.0 对应总纲 0.8/0.9 工程交付；发布成功也不等于取得 1.0 外部竞争领先证明。

状态含义：

- **已实现**：存在运行时代码、闭合 contract、迁移/恢复或测试证据。
- **兼容实现**：由 v0.7 已有正确能力继续提供，并被新内核纳入验证/分区。
- **外部待完成**：需要未受开发团队控制的数据、宿主、竞争系统或机构身份；仓库只实现验证协议，
  不能伪造结果。

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
| 真实中文、英文、代码、法律和长文档 Gold Set | 已实现开发集；外部金标待完成 | `benchmarks/quality/repository-gold-v1.json` 绑定真实仓库文件 bytes/anchor/hash，覆盖五类并运行 lexical/dense/hybrid；每种模式有冻结的质量阈值和非零失败退出码；永久标记 development、非秘密、不可用于领先声明。专家/外部 held-out 金标仍须评测方提供 |
| 默认本地 Dense/Reranker | 已实现 | `knowledge_intelligence.py`；offline、固定 identity/revision/dimension/quantization、audit/input/bytes manifest；损坏/过期拒绝，Authority 不受分数影响 |
| Typed Compiler 质量门禁 | 已实现评分与 contract；真实外部运行待完成 | `benchmarks/typed_compiler/score.py` 计算 precision/recall/F1、hallucination、support、source span、duplicate、review、cross-document；开发 fixture 只校验 scorer 语义 |
| Codex、Claude Code、OpenCode 真实任务 | 外部待完成 | 三宿主薄 adapter、manifest、MCP/no-model lifecycle gate 已实现；当前机器只有 Codex CLI，不能把静态配置或自调用冒充三宿主真实模型任务 |
| 与列明系统公平比较 | 外部待完成 | 17-system registry、official/manual adapters、环境/plan/receipt/resource/raw-output/collection contracts 已实现；所有 actual third-party results 仍是 `pending_execution` |
| 秘密 Held-out | 外部待完成 | v2 external protocol 要求 pre-delivery commitment、external-only labels、paired statistics、完整 failures；开发团队不能生成“秘密”数据后声称未见 |
| 独立机构签名 | 外部待完成 | evaluator-kit freeze/verify 与 detached Ed25519 trusted-key verification 已实现；至少两家真实独立机构身份和签名必须来自仓库外，不能自签替代 |

因此，1.0 的**可实现工程面已闭环**，但竞争证明的外部事实仍未发生，机器状态必须保持
`competitive_claim_eligible=false`。这不是遗漏，而是总纲“真实任务和公平基准证明”与“独立机构
签名”的正确 fail-closed 实现；在本仓库伪造这些产物会直接违反总纲。

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

1. **不能把 1.0 外部证明写成仓库内部已完成。** 真实三宿主、竞争系统、秘密 held-out 和独立机构
   身份都不受开发 Agent 控制；已用机器门禁和 `competitive_claim_eligible=false` 表达，而不是生成
   假报告。
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

外部竞争声明还必须通过 `docs/EXTERNAL_BENCHMARK_PROTOCOL.md`、17-system collection gate、两个
evaluator-controlled secret held-out、paired statistics 和两家独立 trusted-key attestation。任何一个
缺失都必须保持 claim gate 关闭。
