# Analytix 集成设计（未来工作）

## 状态和边界

本文描述 DeepLaw 与 Analytix 的未来集成契约，不证明 Analytix 当前已经具备这些能力，
也不授权修改 Analytix。实施前必须在 Analytix 建立独立 OpenSpec change，并以当时的代码、
已接受规范和测试为准重新复核。

目标不是把 DeepLaw 写进 Analytix 核心，而是让 Analytix 的唯一生产 Go Runtime 安全消费
一个外部、只读、版本化的公共法律能力：

```text
公共 DeepLaw release
        |
只读 law_support MCP
        |
Analytix Go 宿主能力门禁
        |
Provider-visible 法律证据卡
        |
案件事实 receipt + 法律 receipt + Final Evidence Gate
```

DeepLaw 不成为第二个 Agent Runtime，不管理 Analytix thread/session，不读取案件
SQLite/DuckDB，不决定案件事实，也不拥有最终发布权。

## 三类数据必须物理分离

| 数据 | 所有者 | 权限 | 可否进入 DeepLaw |
|---|---|---|---|
| 公共法律 release | DeepLaw | 全局只读、版本化 | 是 |
| 案件文档/对话/事实 | 每个 Analytix 案件项目 | 案件独占、可写 | 否 |
| 交易和统计数据 | 每个案件的 DuckDB | 案件独占、可写 | 否 |

“全局只读”表示所有被授权 Agent 可以查询同一公共 release，不表示法律 Skill、正文或 MCP
schema 对所有 provider turn 永久可见。

## Provider 可见接口

DeepLaw 当前只暴露一个紧凑工具：`law_support`。公开 contract 位于：

- `contracts/law-support.input.v1.schema.json`
- `contracts/law-support.output.v1.schema.json`
- `contracts/law-search-response.v1.schema.json`
- `contracts/law-segment.v1.schema.json`
- `contracts/law-verification.v1.schema.json`
- `contracts/law-release-info.v1.schema.json`
- `contracts/legal-evidence-card.v1.schema.json`
- `contracts/corpus-release-manifest.v1.schema.json`

`law-support.output` 是四种只读操作结果的 closed union；宿主必须按 operation 验证对应分支，
拒绝未知字段，不能只验证 MCP transport success。

DeepLaw `0.1.x` 使用 MCP SDK 的低层 `Server`，只支持本地 stdio transport；它不是 HTTP
服务。握手时发布的是已内联本地 `$ref` 的 closed output schema，宿主不应在运行时依赖网络
解析 GitHub schema URL。

某些宿主会在 provider-visible 名称中加入 MCP server 前缀以避免不同 server 重名。该前缀
只是对同一个 `law_support` leaf 的传输层限定，不代表存在第二个法律工具，也不能形成另一套
输入、输出或权限契约。

操作语义：

| operation | 用途 | 约束 |
|---|---|---|
| `search` | 返回小候选集 | 最多 5 张 evidence card，总摘要最多 6000 字符 |
| `get` | 按稳定 `segment_id` 获取原文 | 只能读取已选定 segment，不做宽泛整库返回 |
| `verify` | 校验 segment/receipt | 重算 segment 文本 hash，并校验 receipt 对 release、document、segment 和已记录 source/segment hash 的绑定；不重哈希原件，不判断案件适用 |
| `release_info` | 获取当前 release 元数据 | 用于宿主预检和版本绑定 |

Provider 输入不得包含：

- `caseId`、thread ID、workspace 路径、案件数据库路径；
- 姓名、身份证号、完整账号、手机号、设备标识或原始附件；
- 未经案件事实门禁验证的长篇案件叙述；
- 让 DeepLaw 决定有罪、罪名或事实真伪的指令。

Analytix 应先将已验证案件事实转换成最小化、去标识化的法律问题，例如行为类型、时间、
法域和待核对要件。DeepLaw 返回法律候选，不能反向获得案件项目读取权限。

## 必须由 Analytix 宿主拥有的权威

### 能力激活

是否向 provider 广告 `law_support` 必须由 Go 宿主在 tool schema 物化前决定。DeepLaw
Skill 文本、MCP description 或模型自行选择都不能成为激活权威。

允许激活的强信号包括：

- 用户明确要求法条、文号、法律依据、案发时版本或司法解释；
- 用户明确要求构成要件、法律定性边界或引文核验；
- 宿主在全案分析的已验证事实阶段之后，启动一个有预算的法律问题筛查子阶段；
- 用户或受信 profile 明确限定了法律工具 scope。

以下条件单独出现不能激活：

- 当前 thread 属于案件项目；
- 出现“诈骗”“账户”“金额”“法院”“异常”等泛词；
- 正在分析交易表、SQL、代码、欺诈检测数据集或文档格式；
- 模型在上一轮提到法律；
- DeepLaw 插件已经安装或 MCP 已连接。

错误激活会改变工具 schema、路由、token 和缓存；因此 false negative 应通过用户显式请求
补救，不能以宽泛关键词换取高 recall。

### Release 绑定

每个使用法律能力的 turn 和正式报告必须固定一个 `release_id`。宿主应：

1. 在 provider 调用前解析并验证已批准 release；
2. 将 release 绑定记录到当前 Context Epoch/turn security context；
3. 校验每个 DeepLaw 响应的 `release_id` 与绑定一致；
4. 对 `get` 和 `verify` 继续使用同一 release；
5. release 中途更新时完成当前 turn，下一 turn 才可显式切换。

DeepLaw 当前在 MCP 进程 lifespan 启动时只解析一次 `ACTIVE` 或 `DEEPLAW_DB`，校验数据库
artifact，并在该进程内复用同一个只读连接；运行中改变 `ACTIVE` 不会切换既有进程的
release。未来 Analytix 仍必须把这一进程/连接生命周期纳入宿主权威：

- 在 provider 调用前完成 MCP 初始化和 `release_info` 预检；
- 将已验证的 `release_id`、数据库 hash、MCP server identity 和 connection epoch 绑定到
  当前 turn；
- 需要显式选择 release 时，在启动进程前设置固定 `DEEPLAW_DB`；
- release 更新时建立新进程/connection epoch，旧进程只完成已绑定 turn 后退出。

仅在每次返回后比较 `release_id` 仍不足以替代上述前置绑定。发生 identity、epoch 或 release
漂移必须拒绝结果并重新建立固定连接，不能静默重试到新 release。

### Receipt 权威

DeepLaw `receipt_id` 证明候选卡与一个 release/document/segment 以及 release 数据库中已记录
的 source/segment hash 的确定性绑定。它不证明原始 DOCX/PDF 当前仍可取得、未被外部替换，
也不是 Analytix 的最终案件证据能力。

Analytix 必须把 DeepLaw 返回视为不可信外部边界，并在本地验证：

- MCP server identity 和 connection epoch；
- transport/semantic success、closed output schema 和字符/条目上限；
- release、document、segment、已记录 source/segment hash 和 receipt 的内部一致性；
- 当前 thread/turn/case/Context Epoch 绑定；
- 查询目的、时间范围和实际覆盖范围。

通过后，Analytix 才能签发自己的 `LegalEvidenceReceipt`，绑定当前安全上下文。外部
`receipt_id`、`safeToAnswer`、citation 或 provider confidence 都不能直接进入 Final Evidence
Gate。

原件真实性、来源站点和法律版本批准仍由受控 DeepLaw release 流程证明；Analytix 不应把
`verify` 成功改写为“已重新核验官方原件”。

### 案件发布门禁

案件法律研判至少区分两种证据：

```text
Case Evidence Receipt     证明案件事实来自当前案件的受信来源
Legal Evidence Receipt   证明引用规则来自固定 DeepLaw release
```

最终陈述还需验证事实与规则的要件映射、反证、缺口和允许措辞。只有法律 receipt 不能证明
案件事实；只有案件事实 receipt 不能证明法律版本。相似案例只能提供参考，不能自动升级为
控制性依据或有罪结论。

## 推荐的 turn 链路

### 普通数据、代码和文档任务

```text
prompt -> 原 Analytix route -> 原 tool manifest -> provider
```

DeepLaw 不参与。不得读取法律数据库、注入 Skill、改变 system prefix 或出现法律化语气。

### 明确公共法律查询

```text
host activation
-> pin release
-> advertise law_support
-> search (0..5 cards)
-> optional get/verify by selected segment_id
-> host-issued LegalEvidenceReceipt
-> answer with version and review notice
```

### 案件事实分析但未要求法律研判

```text
case SQLite/DuckDB/tools
-> Case Evidence Receipt
-> factual/quantitative answer
```

当前是案件项目不能自动增加法律步骤。

### 案件法律研判

```text
verify current case authority
-> obtain Case Evidence Receipts
-> build minimal de-identified legal issue packet
-> activate and pin DeepLaw
-> obtain/verify LegalEvidence Receipts
-> map fact receipts to legal elements and contrary evidence
-> local Final Evidence Gate
-> exploratory answer or reviewed report
```

“全案分析”可以在事实阶段完成后安排一次有上限的法律 issue-screening，但只能输出可能的
问题、证据缺口和需核对规则，不能自动生成确定罪名或有罪结论。

## Analytix 未来代码落点

以下位置是实施前需要复核的最小完整切片，不是本次改动清单。

### Go domain 和 use case

- `packages/runtime-go/internal/domain`
  - 不可变 law capability decision、release binding、candidate receipt 和 host legal receipt；
  - hash、context binding、允许措辞和失败原因等纯值与不变量。
- `packages/runtime-go/internal/app`
  - 新的法律能力激活/预检 use case；
  - DeepLaw outcome 到 host receipt 的验证；
  - 与 `internal/app/evidence` 的 Final Evidence Gate 组合；
  - Context Epoch 的 active/inactive 投影和审计证据。
- `packages/runtime-go/internal/ports`
  - release resolver、只读法律查询、receipt registry 和验证接口；
  - 端口不能暴露案件数据库给 DeepLaw。

新行为应放在 domain/app/ports 的目标层，不应继续扩张 transitional `internal/server` facade。

### MCP 和运行时组合

- `packages/runtime-go/internal/adapters/outbound/mcp`
  - 通过本地 stdio 消费 DeepLaw 低层 MCP server，保留 closed input/output schema、MCP
    error、server identity、connection epoch 和只读提示；
  - 对超限、未知字段、release 漂移和 receipt mismatch 失败关闭。
- `packages/runtime-go/internal/runtimeapp/app.go`
  - 组装 capability gate、release binding 和 receipt verifier；
  - 法律服务不可用不影响非法律 use case。
- `packages/runtime-go/internal/server/tool_catalog.go`
  - 在 MCP schema 物化之前应用 host-owned DeepLaw advertisement filter。
- `packages/runtime-go/internal/server/agent_loop.go`
  - 使用过滤后的 schema 计算 route、tool manifest hash 和 provider request；
  - 记录激活与 release 绑定证据，但不记录案件事实或法律正文。

当前 `PromptRouteForAdvertisedTools` 会因任何 provider-visible MCP/Skill 工具将普通路径升级为
`tool_agent`；当前通用 MCP search 在未启用、缺少搜索函数或已有 `toolScope` 时可能返回全部
工具。因此不能只把 DeepLaw 注册为默认全局 MCP 后依赖现有通用搜索自动隔离。

### Desktop contract 和 UI（仅在边界确实变化时）

- `packages/runtime/src/contracts`：若增加 runtime API/诊断，更新公开 schema/types；
- `src/shared/app-settings-types.ts` 和 `src/shared/app-settings-runtime.ts`：只保存宿主开关和
  release policy，不复制法律正文或旧设置树；
- `src/main/analytix-process.ts`：复核插件 MCP 注册、固定 release 环境和进程失败隔离；
- `src/preload`、`src/renderer/src`：只有新增用户可见诊断、版本状态或人工复核交互时才改。

不需要为 DeepLaw 新建 TypeScript Agent loop、第二套会话存储、法律聊天历史或 runtime
backend switcher。

## Inactive 零影响硬要求

DeepLaw 安装但未激活时，相同 prompt、模型、设置、workspace 和历史必须与“未安装
DeepLaw”基线等价。

至少验证：

| 信号 | inactive 要求 |
|---|---|
| `promptRoute` | 完全相同，不因 MCP 变为 `tool_agent` |
| provider-visible tool names/schema | 完全相同，不出现 `law_support` 或其 host-qualified 名称 |
| tool manifest/schema hash | 完全相同 |
| stable prefix 和 prefix items hash | 完全相同 |
| provider history、dynamic context、turn tail | 不含 DeepLaw 内容且完全相同 |
| provider request shape/body | 去除已批准的 volatile ID 后完全相同 |
| prompt/input tokens | 完全相同 |
| Context Epoch | 没有 active DeepLaw source 或虚假 epoch bump |
| DeepLaw 调用和数据库读取 | 0 |
| 普通任务错误面 | DeepLaw 离线、损坏或更新均不可见 |

资源门禁还应比较冷启动、p50/p95 latency、内存、CPU、文件描述符和缓存命中。默认应按需
启动或保持 provider-invisible；仅仅“模型最终没调用工具”不算零影响。

### Passive A/B 测试集

关闭与安装两组至少覆盖：

- Excel/CSV 金额统计和 top-N；
- DuckDB/SQL 查询；
- 包含“诈骗”字段的欺诈检测数据集；
- 代码中的法律相关字符串；
- 普通 DOCX/PDF 格式转换；
- 一般写作、翻译和产品分析；
- 案件项目中的纯资金流分析。

两组应产生相同 route、schema、request 和工具调用序列。法律回答质量不用于抵消 passive
退化；任何 schema/token/route 差异都阻止默认集成。

## Active 功能测试

至少验证：

- 精确法名、文号和条款优先命中；
- `as_of` 返回固定 release 中正确候选，并对未知时效标记人工复核；
- 泛词只返回导航或极少候选，不灌入整包材料；
- `search -> get -> verify` 全链 release、segment hash 和 receipt 绑定一致，不把成功结果
  表述为重新哈希原始法源；
- DeepLaw receipt 不能直接绕过 Analytix host receipt；
- 当前案件但未请求法律时不激活；
- 去标识化 query 不含案件 ID、路径和 PII；
- 无命中不升级为“不存在/不构成”；
- release 切换、服务断开、schema 变化和 receipt mismatch 均失败关闭；
- 法律子任务失败时普通 DuckDB/SQLite/代码任务继续运行；
- 正式案件结论缺少任一 Case/Legal receipt 时无法发布。

## 错误与降级

- DeepLaw 不可用：明确标记法律支持不可用；普通任务继续。
- release 未批准或 hash 错误：不调用 provider 继续完成法律结论。
- OCR/时效待复核：只能显示受限候选和复核提示，不能提升为现行确定规则。
- MCP transport success 但 semantic/error schema 失败：按失败处理。
- 无命中：保留查询范围说明，不回退模型记忆或未核验网络搜索。
- 报告需要法律依据但门禁未通过：法律部分保持 blocked/incomplete，不生成正式发布 receipt。

## Context Epoch 与持久化

DeepLaw 在 Analytix 中应遵循已接受的 Context Epoch 规则：

- 未激活：不改变 history、stable prefix、tool schema 或 request shape；
- 已激活：只记录有界的 release binding、receipt 和 sanitized activation reason；
- 法律原文和搜索候选不写入 stable prefix；
- release 元数据和 host receipt 可作为 `store-only` 审计状态；
- provider-visible evidence 只存在于当前受权 turn-tail/dynamic context；
- compact、resume、fork 和 restart 不能把旧 receipt 提升到新 context；
- 正式报告保存确切 release 和 receipt，不依赖后来变化的 ACTIVE 指针。

会话 transcript 可以保存用户和最终回答，但检索候选、原始工具输出和法律正文的持久化必须
遵守有界 public projection。不得把每次对话自动写回 DeepLaw Wiki 或案件知识真源。

## 实施顺序

1. 建立 Analytix OpenSpec change，先固定 capability、release 和 receipt contract；
2. 编写 inactive A/B hard gate，暂不连接真实 DeepLaw；
3. 在 schema 物化前实现 host advertisement filter；
4. 实现固定 release 连接和 DeepLaw closed-output 验证；
5. 签发 host `LegalEvidenceReceipt` 并接入 Final Evidence Gate；
6. 加入 failure isolation、Context Epoch、restart/fork/compact 测试；
7. 用受控候选 release 做 active 功能测试；
8. 所有 passive、隐私和发布门禁通过后，才考虑默认安装。

在第 2 步无法证明 inactive 零影响时，不应把 DeepLaw 作为 Analytix 默认启用的全局 MCP。

## 明确不做

- 不在 Analytix 中复制 DeepLaw SQLite 或实现第二套法律检索逻辑；
- 不把法律 Skill 写入全局 stable system prefix；
- 不把当前案件绑定当作法律能力自动激活信号；
- 不把公共法律库与案件 SQLite/DuckDB 或全局向量库合并；
- 不让 provider 指定 case/path/release 或直接签发权威 receipt；
- 不让 LLM、Wiki、向量或相似案例决定效力和案件结论；
- 不因法律服务故障阻断普通数据、代码、文件或会话能力；
- 不在本次 DeepLaw 开发中提前修改 Analytix 代码。
