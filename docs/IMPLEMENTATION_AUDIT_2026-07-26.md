# DeepLaw 2.0 施工方案—源码—证据复核

复核日期：2026-07-26
目标版本：`0.4.0` 候选
复核对象：[`改造施工方案.md`](../改造施工方案.md)、当前源码、CLI、MCP、插件、数据契约、
Legal Pack 重建和评测/宣称门禁。

> 历史快照说明：本文绑定 `v0.4.0` 施工阶段，不作为当前 `v0.5.0` 源码或性能证明。
> `v0.5.0` 的当前状态、Discovery 消融、规模证据和未满足外部门禁见
> [`改造施工方案.md`](../改造施工方案.md) 与
> [`benchmarks/knowledge-os-v0.5.0-candidate-2026-07-26.json`](../benchmarks/knowledge-os-v0.5.0-candidate-2026-07-26.json)。

## 结论

当前工作树中没有已知未关闭的 P0/P1 实现缺陷。这里的“关闭”只表示已发现的软件安全、隔离、
一致性、可复现性和上下文正确性问题已由代码与测试处理，不表示外部性能领先已经得到证明。

本轮实际发现并关闭九类 P1：

1. Context Capsule 可能被首个长 Asset 独占，且前缀截断可能丢失真正命中位置；
2. 超长 Agent task 只保留前 32 个检索词，尾部实体即使明确也可能完全不可发现；初次修复后，
   excerpt 仍可能围绕任务前缀截取，形成“命中 Asset、交付错误片段”；
3. 大 source 逐 Asset 审核会重复执行全库完整性重放，10 万候选无法形成可用 CLI 闭环；
4. `source_compiled` 事件直接携带全部 fragment/Asset ID，在 10 万规模超过事件载荷上限；
5. 完整性核对同时 `fetchall()` 多份大表，关系查询计划在无边时可能扫描整个 Asset inventory，
   CLI 摄取回执也可能返回无界 ID 数组；
6. Legal Pack `release_id` 的 derivation 未完整绑定 review governance 与 build-report 身份，
   不同治理元数据可能落入同一逻辑 ID；
7. 本机默认 `ACTIVE` 曾指向历史 release，不能把它误写成当前 v0.4 构建证据；
8. 初版外部宣称门禁虽检查签名，却还允许证据拼接、无贡献评测方计数、结果布尔值自报，且
   Holm–Bonferroni 只写在协议中而未执行。
9. 宣称门禁后来仍信任评测方提交的 bootstrap CI、p 值和 `superior` 布尔值；现在从精确绑定
   的候选/基线逐题报告重新计算完整比较，伪造并重新签名也不能通过。

关闭后的边界：

- 通用 Knowledge Asset、官方 Legal Pack、用户私有法律资料和 Analytix 案件项目仍是四个不同
  scope；DeepLaw 不摄取案件项目；
- Agent 可见 MCP 仍完全只读；任何长期知识变化都要走 owner CLI、proposal 和人工审核；
- 法律正文、来源、版本、时效、关系和 release 内审核状态不能被 Agent 修改；
- “全面超过所有知识库”永久被机器门禁拒绝；当前外部证据状态仍为
  `pending_external_execution`。

## 分层真源与修改权限

| 数据范围 | 规范真源 | Agent 权限 | owner/团队更新方式 | 已核对实现 |
| --- | --- | --- | --- | --- |
| 通用 Knowledge Asset | owner-only SQLite vault + 原件 fragment | 只读 `knowledge_support` | CLI proposal、审核、替代、撤销 | `knowledge_store.py`、`knowledge_mcp_server.py` |
| 官方 Legal Pack | 签名目录 + 本地不可变 release | 只读 `law_support` | 团队递增签名目录，用户显式 install/update | `official.py`、`store.py`、`mcp_server.py` |
| 用户私有法律资料 | 独立 owner-only source/release/ACTIVE | 只读私有查询 | 用户显式 add/delete 后重建快照 | `private_library.py` |
| Analytix 案件项目 | Analytix 自有附件、会话、SQLite/DuckDB | DeepLaw 无权访问 | 仅 Analytix | 两个 Skill 的边界、CLI 确认和 schema 负例 |

同账户任意 Shell 不是 DeepLaw 能自行建立的安全边界。宿主若授予 Agent 写
`~/.deeplaw` 或执行管理 CLI 的权限，必须由宿主工具策略或独立 OS 身份隔离；项目没有伪称
MCP 的 `readOnlyHint` 能约束通用 Shell。

## 十四章施工方案核对

| 章节 | 源码/契约落点 | 验证证据 | 结论 |
| --- | --- | --- | --- |
| 一、最终定位 | 两个独立插件、两个 MCP、独立 storage | plugin/MCP tests | 已实现；不是 Agent Runtime 或会话库 |
| 二、方案修正 | SQLite 真源、fragment/Asset 分层、人工审核 | asset/package/Markdown tests | 已实现 |
| 三、最终架构 | `knowledge_support` / `law_support` 分离 | plugin schemas、stdio MCP | 已实现 |
| 四、Asset 规范 | `knowledge_models.py`、`knowledge_store.py` | lifecycle、expiry、trust、tamper tests | 已实现 |
| 五、Knowledge Compiler | `knowledge_compiler.py`、`extract.py`、文档引擎 | DOCX/PDF/DOC/TXT/code 与风险负例 | 已实现 |
| 六、Context Compiler | `context_compiler.py` | 预算、公平分配、命中位置、关系、stale tests | 已实现并在本轮修正 P1 |
| 七、受控成长 | feedback/debug 只生成 proposal | CLI acceptance、fabricated Capsule 负例 | 已实现 |
| 八、关系与人类视图 | reviewed one-hop relation、Markdown 投影 | relation/Markdown safe replace tests | 已实现 |
| 九、`.dlk` | `knowledge_package.py` | 重放身份、zip 攻击、trust laundering tests | 内容完整性已实现；发布者身份仍是 P2 |
| 十、Agent 接入 | Codex/Claude/OpenCode 独立配置 | manifest/schema；Codex 实机历史证据；Claude/OpenCode 当前仅静态契约 | 已实现，验证等级已区分 |
| 十一、施工清单 | 见下一节逐项映射 | 全仓测试与 CLI 验收 | 已实现 |
| 十二、发布门禁 | lock、ruff、pytest、build、schema、diff | 本轮重新执行 | 已实现 |
| 十三、研究门禁 | external protocol、scorer、claim gate | dev 诊断 + pending gate | 工具已实现；外部结果尚未取得 |
| 十四、验收标准 | 来源、预算、gap、隔离、不可自学习 | 合同/攻击性测试 | 当前候选满足已编码门禁 |

## “已经完成的施工”逐项源码核对

| 施工项 | 主要实现 | 主要测试/证据 | 状态 |
| --- | --- | --- | --- |
| Asset 契约与 SQLite vault | `knowledge_models.py`、`knowledge_store.py`、5 份 knowledge schema | `test_knowledge_assets.py` | 闭环 |
| owner-only、物理隔离、symlink 失败关闭 | vault path/mode 校验 | owner/symlink/双 vault tests | 闭环 |
| source/fragment/Asset 分层 | compiler + source/fragment tables | source compiler tests | 闭环 |
| proposal/quarantine/approve/supersede/revoke/expiry | evented lifecycle | lifecycle/semantic key/working expiry tests | 闭环 |
| 人工复核关系 | relation identity + reviewed evidence | relation bound/self-loop tests | 闭环 |
| PDF 与通用编译 | compiler、document engine、vision pipeline | extraction/document tests | 闭环 |
| Context Capsule | Context Compiler + Capsule schema | `test_context_compiler.py` | 闭环 |
| Capsule/Asset/audit verification | digest、audit anchor、current source rehash | tamper/stale/source deletion tests | 闭环 |
| event chain 与全状态重放 | `verify_audit_chain`、`verify_state_integrity` | DB/FTS/status tamper tests | 闭环 |
| 安全校验缓存 | inode/size/mtime/ctime/revision/audit-head key | changed DB/source tests | 闭环 |
| quarantine 双确认与 trust 阻断 | approval guard、user trust enum | instruction/self-assert tests | 闭环 |
| `legal_authority=false` | Asset/Card/Capsule/MCP authority boundary | schema/MCP/CLI tests | 闭环 |
| 一跳关系与选择原因 | bounded reviewed relation expansion | relation/contradiction tests | 闭环 |
| 非案件显式确认 | CLI/MCP input contract | ingest/context/feedback negative tests | 闭环 |
| Feedback 绑定真实 Capsule | Capsule 文件与当前 vault verification | fabricated ID test | 闭环 |
| Debugger/feedback proposal | CLI + store proposal | debugger/feedback tests | 闭环 |
| Markdown/Obsidian 投影 | `knowledge_markdown.py` | deterministic/safe replace/literal rendering | 闭环 |
| `.dlk` 内容可复现与不可信导入 | `knowledge_package.py` | package identity/zip/trust tests | 闭环 |
| 独立只读 MCP | `knowledge_mcp_server.py` | tool list、unknown/write rejection | 闭环 |
| 宿主 Shell 边界 | Skill/安全文档 | plugin assertions | 已诚实限定 |
| 五种封闭 MCP operation | input/output JSON Schema | Draft 2020-12 + stdio tests | 闭环 |
| 未配置 vault 的安全发现 | MCP 启动与无路径错误 | missing-vault stdio test | 闭环 |
| CLI 全生命周期 | `knowledge_cli.py` | `test_knowledge_cli_acceptance.py` | 闭环 |
| 大 source 原子审核 | `approve-source`、source membership commitment | 10 万资产 CLI 与篡改/幂等/回滚测试 | 闭环 |
| 大 vault 完整性与关系读取 | 流式 event/search replay、有界 relation candidate CTE | 10 万资产诊断与 SQLite progress-handler 负例 | 10 万工作区间闭环 |
| 三宿主适配 | plugin bundles、OpenCode agent/config | plugin tests | 闭环 |
| Legal Pack 保留 | 原有 CLI/MCP/search/build | 全仓 Legal Pack tests | 闭环 |
| 文档 IR v2 字段适配 | document engine adapter | structured-content tests | 闭环 |
| 固定模型清单与离线摄取 | `document_engine_models.py` | extra/missing/tamper/env tests | 闭环 |
| DOCX XML 安全 | defused OOXML v2 | DTD/entity tests | 闭环 |
| PDF 风险页精确隔离 | Document IR page/segment flags | 当前重建：15 页、8/3237 segment | 闭环但仍非人工逐字认证 |
| 缺省 `as_of` 历史法源隔离 | temporal admission | v0.4 bucket cases | 闭环 |
| 版本化评测夹具 | immutable historical/current snapshots | snapshot hash test | 闭环 |
| 文档一致性 | README/architecture/security/本审计 | link/diff checks | 闭环 |

## 本轮新增的可验证改进

### Context Compiler

- 长 Asset 不再独占全部正文预算；
- excerpt 围绕查询命中位置选取，并保持可由原 statement 精确验证；
- 同一编译 section 的多个 part 先去拥挤，再给其他来源机会；
- 英文问句脚手架词不再驱动 FTS；
- 低于最佳词法候选相对阈值的尾部噪声失败关闭；
- budget gap 仍保留，系统不会把被排除内容伪装成“不存在”。

60 题公开开发诊断记录在
[`longmemeval-s-dev-2026-07-26.json`](../benchmarks/external/longmemeval-s-dev-2026-07-26.json)。
它已被开发团队看过并用于修改，机器标记为 `claim_eligible=false`。逐类型复核没有用总平均
掩盖弱项：10 个偏好 case 的 Capsule Hit@1 为 `0.20`、Recall@5 为 `0.60`、无关率为
`0.85`，其余 50 题 Hit@1 为 `0.98`。当前实现擅长已明确写入的事实、更新和时间信息，不把
原始会话中的隐含偏好伪装成已经可靠提炼的 durable Asset。

### Legal Pack release 身份

derivation 现在除来源、Document IR、segment、关系和 extractor 外，还绑定：

- review overlay hash、reviewer kind、review scope、时效/再分发状态和覆盖数；
- collection scope / private library ID；
- 完整 build-report 身份（计算 ID 时使用 `release_id=pending` 的闭合内容）。

SQLite 的物理 SHA 仍作为独立 artifact identity；`release_id` 表示逻辑 derivation，正式报告和
Agent turn 必须同时固定 `release_id + database_sha256`。

### 规模与 Context 正确性

- 长任务检索按固定上限覆盖头、中、尾，并优先保留可识别的精确标识；
- 通用 Knowledge OS 的 excerpt 使用同一尾部覆盖，Legal Pack 继续使用原有前缀语义，37 项
  法律 smoke 的结果、Duty 与 gap 完全未漂移；
- `approve-source` 对精确 source membership 做一次完整性重放和一个原子事务，来源缺失、
  篡改、歧义、撤销/替代或 quarantine 未二次确认均失败关闭；
- source 编译事件由无界 ID 列表改为 count + membership SHA-256，验证器同时兼容历史事件并
  从当前 fragment/Asset 绑定重算；
- 10 万资产、100 个长查询的真实 CLI/常驻读取报告绑定实现 hash；常驻 search p95
  `0.82 ms`、context p95 `1.28 ms`，冷完整性重放 `5.85 s`、峰值约 `443 MB`；
- 当前只承诺 10 万已测工作区间；更大规模需按 project/domain vault 分区并重新冻结评测。

### 外部证明链

[`protocol-v2.json`](../benchmarks/external/protocol-v2.json) 已被 canonical SHA-256
`d3a472c48df3d18d7f43310bb55658ad28d46a8691a981bb883074ec39d1f369` 冻结；
v1 只保留历史且被门禁拒绝。
声明门禁要求：

- 十个预注册套件、55 个具名 baseline 及每个套件的全部预注册 baseline × dimension 比较，
  不能挑结果；
- 候选和基线逐题报告、比较、原始输出、模型/预算/成本/硬件与时间记录进入同一 suite manifest；
- 独立评测方用 Ed25519 attestation 绑定 suite manifest，而不是只签一张平均分截图；
- 至少两家真正对某个当前 run 有贡献的机构；签署无关 suite 的“幽灵评测方”不计数；
- 1% 预注册最小优势（任务成功、必要上下文召回）、严格非劣安全门禁；
- 配对 bootstrap、逐题均值复算和 Holm–Bonferroni family-wise gate；
- 门禁从候选/基线逐题报告重算完整 comparison，不能自报 CI、p 值或通过布尔值；
- public development、缺失 case、重复 ID、开放 JSON、路径逃逸、symlink、hash 不符和证据拼接
  全部失败关闭。

完整 synthetic pass-path 和所有 pending/篡改负例均有测试。真实外部结果尚未运行，因此当前
不会生成任何领先声明。外部候选已经冻结为 commit
`51e50172e0c2d920eb51c8105689c74efa9d42da`；连续两次隔离构建生成相同 wheel SHA-256
`e80a34d064879189730b55827e724bf4c23405301f602a6ea5c3daeaf4ed5b93` 和 sdist SHA-256
`e16a993dc367eabedccdd852639663f0fe1acfd1546384c81e77a9468aa82c7c`。

## 当前证据与不能宣称的内容

| 证据 | 当前状态 | 可支持 | 不能支持 |
| --- | --- | --- | --- |
| 全仓与攻击性测试 | 通过后方可提交 | 已编码契约无已知回归 | 未编码世界的正确性 |
| Legal Pack 37 case | 37/37 smoke | 当前 28 份资料的定位/分桶/receipt | 法律充分性、专家认证 |
| 60 题公开开发诊断 | `claim_eligible=false` | 发现并修复 Context 缺陷 | 对外领先 |
| 10 万资产规模诊断 | `claim_eligible=false` | 当前本地 vault 已测工作区间 | 自然语言泛化或百万规模 |
| 十套件外部协议 | 已冻结、门禁可执行 | 未来结果的公平边界 | 当前已有成绩 |
| 两个第三方 hidden + 独立复现 | 尚未取得 | 完成后可生成协议限定文案 | “全面超过所有知识库” |

永久不能宣称：

- 覆盖未参评、未公开或未来出现的“所有知识库”；
- AI 视觉复核等于人类逐字法律审查；
- `trust`、hash、签名或 retrieval score 等于法律事实正确；
- DeepLaw MCP 能约束宿主授予的通用 Shell；
- 通用 Asset 能替代 Legal Pack，或 Legal Pack 能保存 Analytix 案件事实。

## 下一步执行顺序

1. 已固定候选提交、可重复构建 wheel SHA 和协议；后续不得用测试结果调本候选阈值；
2. 由独立评测方接收固定 wheel，运行 v2 中的 LongMemEval-V2、MemoryAgentBench、Memora、STATE-Bench、
   Agent Memory Benchmark、LegalBench-RAG、Legal RAG Bench、Agent Security Bench，
   以及两个秘密套件；
3. 在相同 reader、语料、问题和 Token 预算下运行协议列出的全部 baseline；
4. 两家机构分别签名 suite evidence manifest；
5. 运行 `claim_gate.py`；只有退出码 `0` 时使用其自动生成的限定文案；
6. 失败则公开失败样本和成本，修复进入新版本/新协议，不能回填本协议成绩。

更具体的外部交付格式见
[`EXTERNAL_BENCHMARK_PROTOCOL.md`](EXTERNAL_BENCHMARK_PROTOCOL.md)。
