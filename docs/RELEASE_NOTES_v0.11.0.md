# DeepLaw v0.11.0 release notes

Release date: 2026-07-30  
Release status: formal GA after the exact-tag release workflow succeeds  
`commercial_release_eligible=true`  
`quality_protocol_eligible=true`  
`competitive_claim_eligible=false`

## Implemented

- Living Wiki Compiler 的 closed Packet/Plan/Receipt 协议、持久化 Compilation Run Saga、
  原子 Knowledge/Relation commit、恢复、abort、projection retry 和 freshness propagation。
- Compiled-first 与 purpose-aware evidence-first 检索；raw fallback、uncompiled source、
  stale knowledge、预算和选择原因均进入可核验 Query Plan。
- Rich Living Wiki source/object pages、分片索引、fragment shards、局部/社区/全局 Canvas，
  以及删除 FTS、dense、graph、Wiki、Canvas、cache 后的确定性重建。
- CLI、MCP 和稳定 Python API 复用同一领域协调器；`knowledge_support`、
  `knowledge_sink`、`law_support` 保持独立进程与权限边界。
- 对 exact signed catalog 的显式 `--rebuild-current-catalog` owner 操作：仅允许已安装、
  签名验证通过且提供 exact local source root 的目录重解析；保留旧 immutable release，
  原子切换新 release。
- Living Wiki frozen CLI quality suite、28-source Authoritative Pack 决策矩阵和
  同条件 0.10.0 baseline comparison、release manifest v4。Manifest 绑定 exact commit、
  tree、version、tag、wheel/sdist、contracts、migration identities、three-OS、
  three-host、fresh-wheel、rollback、release notes、已知限制和未声明能力。
- 修正 exact identity 被 kind 排序覆盖，以及 item/character budget 在双通道分配时
  超配的问题；`limit=1` 现在在所有 purpose policy 下保持总预算。
- Derived-state rebuild 会安全重建缺失的受控目录，同时继续拒绝 symlink 或非目录目标。

## Verified

- 28 份官方资料 exact bytes 均匹配 signed catalog；先完成 snapshot/restore inventory
  验证，再逐份 dry-run。13 份 `no_action`，15 份 `reparse_source_ir`，没有伪造
  Source Revision，也没有通过普通 Source ingest 绕过 Authoritative Pack。
- 两次隔离重建的 build report 与 SQLite release bytes 分别完全一致：
  build report `549d677c41ec6e3aa3920462fa870d7fd1af9e5f2ba278422ca72503e2f88d90`，
  database `ff4bc58e3a77585dccb8b22bd049b50612b0a8c85f7fb858551ea424021fbdc0`。
- 新 active official release 为 `lawrel_1bee97015ee440c71ea993b083a89005`；
  28 documents、3237 segments、111 relations。旧 release 保留并可显式 pin/verify。
- 同一 37-case Legal Pack evaluation 上，baseline 与新 release 的 overall、
  retrieval、constraint、receipt verification 均为 1.0；Hit@1 为
  0.9705882352941176，MRR 为 0.9852941176470589，没有质量回退。
- Frozen Living Wiki suite 使用第一方 CLI 完成 source、compile、query、context、
  freshness、withdrawal、gap、verify、Wiki/Canvas 和 destructive rebuild 闭环；
  报告明确记录指标、环境、预算、延迟、失败和限制。Exact baseline commit 为
  `42382b264f4297965c25aaac6e85619e9e0d49b7`，其可复现 wheel SHA-256 为
  `9bda60831e4380092c9a3bdb80103b5ec8abbf5a2be0adf6ffd57f61cfa46ca0`；
  baseline、fresh wheel 和 comparison 三份报告均作为正式发布资产。
- 本地门禁与 exact candidate 的最终命令、结果记录在
  [`V0_11_ACCEPTANCE_MATRIX.md`](V0_11_ACCEPTANCE_MATRIX.md)；正式资产中的 manifest
  和 post-release report 是发布字节的最终证据。

## Externally verified

- 正式 Release 仅在 exact `v0.11.0` tag 的 GitHub Actions Commercial GA workflow
  取得 Linux、macOS、Windows 零 skip mandatory suite 后发布。
- 同一 workflow 固定 Codex `0.145.0`、Claude Code `2.1.220`、OpenCode `1.18.8`
  完成 no-model discovery、MCP、deterministic fake-Agent、隔离、拒绝未授权写入和
  lifecycle 验证。
- 发布 workflow 对 wheel、sdist、OCI、SBOM、license inventory、OpenVEX、
  Evaluation Protocol、Living Wiki quality report、28-source matrix 和文档生成
  SHA-256、Sigstore OIDC bundle 与 GitHub provenance attestation。
- 发布后 job 从公开 GitHub Release 重新下载正式资产，校验 checksum/signature/
  provenance，安装 exact wheel/sdist，并再次运行 Living Wiki formal-release quality
  smoke。任何一项失败都会阻止或使 Release job 失败。

## Not verified

- 真实模型任务没有作为 v0.11.0 三宿主验收运行；no-model lifecycle 与真实模型任务
  明确分开，`real_model_task_e2e=not_executed`。
- 未知、未接入或不遵守 DeepLaw 版本化 CLI/MCP/Python 契约的 Agent 未实机验证。
- 28-source evaluation 是 deterministic/source-bound evaluation，不是人工法律结论、
  外部机构认证或完整语义 relevance gold。
- `review_required` 的 PDF extraction 风险仍保持显式，未被描述为人工审核或官方认证。

## Deferred

- DeepLaw Desktop、大型 Web UI、云 SaaS、多租户、团队 RBAC、远程 canonical
  database 和通用 Agent Runtime。
- 未经 owner 既有可信 workflow 的新制品发布渠道。
- 全部真实模型/插件组合和命名竞品的冻结、配对、成本与 failure-case 比较。

## Not claimed

- 不声称最强、领先、超越 Guanlan、超越 Obsidian、超越 Tolaria 或 SOTA。
- 不声称所有未知 Agent/模型/插件都已实机验证。产品结论仅为：任何遵守 DeepLaw
  版本化 CLI/MCP/Python 契约并获得 owner 显式授权的 Agent，都可以通过相同的
  受治理编译事务接入。
- 不声称 Agent-generated legal interpretation 具有 legal Authority；其
  `legal_authority=false`。
- 不声称 DeepLaw 是 Obsidian/Tolaria 替代品、法律裁判系统、模型宿主或远程控制面。

## Compatibility and rollback

- Python requirement remains `>=3.11`; formal gates cover Python 3.11、3.12、3.13。
- v0.11.0 retains additive migration and rollback paths from old Vaults and the v0.6.0
  distribution fixture. Snapshot/restore is required before migration or authoritative reparse.
- Previous official releases remain immutable and available for historical pinning. Catalog
  sequence rollback、unsigned catalog、rewritten same-sequence catalog 均 fail closed。
