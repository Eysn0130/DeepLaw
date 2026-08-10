# v0.13 三产品 Outcome Package Owner Protocol

状态：`Benchmark-only / not qualification / assembly disabled`。

本协议定义一个由 Owner 组装、由本地 verifier 重新打开校验的内容寻址 manifest。它
不是 release manifest、不是 Gold、不是 scorer，也不会把 development 结果升级成
qualification。`contracts/v013-product-outcome-package.v1.schema.json` 是闭合结构，
`benchmarks/v013/product_outcome_package.py` 是唯一的 benchmark verifier；它复用
`benchmarks/release/provenance_gate_result.py::validate_gate_result`，不复制 Gate Result
或现有 scorer 的评分逻辑。

## Owner workspace、目录和 mount

synthetic dry-run 可以使用单一、`owner_only` 的 `package_workspace` mount；
`owner_bound_external` 不可以。外部 package 必须为每个专用隔离域声明唯一 `mount_id`，
并在调用 verifier 时为所有 mount 提供完整的 `roots={mount_id: directory}`；不存在
default-root fallback。manifest 只存相对路径，不把本机绝对路径写进 JSON。每个 resolved
root 必须是目录、不可为 symlink，所有文件 `read_only=true`。compiler-only 与
evaluator-only 隔离域不得解析到同一 root。同一 evaluator workspace 可以由
`outcome_output` 和 `validator` 两个专用 mount id 共同指向，以便既保持 artifact purpose
闭合，又让 Gate Result 的相对输入引用可重算。下表是精确的目录布局（`<workspace>` 是
外部 Owner workspace，不是仓库根目录）：

| 目录 | manifest `purpose` | 可见性 | 内容 |
| --- | --- | --- | --- |
| `<workspace>/candidate/` | `candidate` | `compiler_evaluator` | 精确 candidate wheel；commit/tree/package version 绑定 |
| `<workspace>/protocol/` | `protocol` | `owner_evaluator` | `qualification-protocol-v1.json` 原始 bytes；compiler 不可见 |
| `<workspace>/thresholds/` | `thresholds` | `owner_evaluator` | frozen threshold/metric catalog 原始 bytes；compiler 不可见 |
| `<workspace>/classification/` | `classification` | `owner_evaluator` | v0.13 classification 原始 bytes + SHA |
| `<workspace>/corpus/development/` | `development_corpus` | `compiler_only` | development source corpus；可调试，不是 holdout |
| `<workspace>/corpus/qualification_holdout/` | `qualification_holdout` | `compiler_only` | Owner 外部 source corpus；Gold 不得挂入 compiler |
| `<workspace>/corpus/final_blind/` | `final_blind` | `compiler_only` | freeze 后才可挂载；失败后的 replacement 必须新 bytes |
| `<workspace>/gold/` | `gold` | `evaluator_only` | 对应 corpus Gold；compiler 永不获得 Gold |
| `<workspace>/receipts/compiler/` | `compiler_receipt` | `owner_evaluator` | compiler isolation receipt |
| `<workspace>/receipts/evaluator/` | `evaluator_receipt` | `owner_evaluator` | evaluator isolation receipt |
| `<workspace>/outputs/<product>/` | `outcome_output` | `owner_evaluator` | continuity、wiki、legal 的 raw output 与 Gate Result |
| `<workspace>/scorers/` | `scorer` | `evaluator_only` | scorer source 和 executable 原始 bytes |
| `<workspace>/validators/` | `validator` | `owner_evaluator` | 专属 Gate Result validator source 和 executable bytes |
| `<workspace>/attestations/` | `attestation` | `owner_evaluator` | owner 与 evaluator attestation 原始 bytes |

每个 artifact 的 `root` 必须指向已声明 mount，artifact kind、mount purpose 和 visibility
必须相容。Gold、scorer、protocol、threshold、classification、validator、raw output、
Gate Result 和 evaluator receipt 都不能进入 compiler-visible mount；corpus 也不能借用
Gold/scorer/output mount。duplicate mount、缺失或额外 root、越界相对路径、symlink、
size、file SHA 或 record digest 任一项都会失败关闭。

这些检查只证明 manifest 引用的 bytes、root 解析和观察记录一致。JSON attestation 与
isolation receipt 本身不能证明 OS sandbox。独立 qualification 仍必须由专用执行环境证明
不同 OS 用户或容器、read-only mount、compiler/evaluator 进程隔离、网络策略和不可见目录。

## 执行顺序

1. Owner 冻结 candidate commit/tree、package version、wheel bytes；读取并记录 protocol、
   thresholds、classification 的完整文件 SHA。先不要写任何 outcome 的 `passed`。
2. 为三层分别记录 `role/source/frozen/status`。development 可调参；qualification
   holdout 一旦被诊断或调参，必须写 `diagnostic_or_tuning_used=true` 并将状态降为
   `downgraded_development`，永远不能作为 passed qualification。final blind 只有在
   candidate freeze 后才可挂载。
3. 在 compiler mount 中只放 candidate wheel 与所选 layer 的 source corpus。compiler
   receipt 必须证明 `gold_access=false`、`scorer_access=false`、
   `repository_source_access=false`、`expected_identity_access=false`、
   `ambient_secret_access=false` 且 inputs read-only。
4. 用现有三 scorer 生成各自 raw output；raw output 是观察材料，不是 pass authority：

   | product id | 现有 scorer source |
   | --- | --- |
   | `continuity` | `benchmarks/v013/score_continuity.py` |
   | `wiki` | `benchmarks/v013/score_evidence_wiki.py` |
   | `legal` | `benchmarks/v013/score_legal_exact_evidence.py` |

   scorer executable 的实际 bytes 也必须作为独立 artifact 绑定，不能只填一个路径或
   hash 字符串。
5. evaluator 只能 read-only 读取相应 raw output 与 Gold。evaluator receipt 必须证明
   `compiler_process_access=false`、`candidate_mutation=false`、`output_mutation=false`、
   `read_only_inputs=true`。每个 product 必须生成自己的 provenance-bound Gate Result；
   其 validator source/executable bytes 同样作为 artifact，并与 Gate Result 内部绑定
   完全相同。
6. verifier 先重算 package/artifact/event digests，再调用现有
   `validate_gate_result`。该通用 validator 只验证 provenance envelope，不证明 scorer
   或 validator 实际执行，也不从 Gold 重算产品结论。因此 v1 明确拒绝 Gate Result、raw
   output 或 outcome 的 `passed`。只有三个产品各自的确定性 validator、可信执行链及其
   外部冻结 identity/hash 都完成后，才可用 additive contract 接受 pass；不得原地放宽
   v1。`not_executed` 结果不能携带 pass Gate Result。
7. Owner 写入一条 owner attestation、一条 evaluator attestation 及 lifecycle event log。
   `final_blind_failed` 事件必须绑定失败时的 corpus + Gold；随后
   `final_blind_replaced` 必须绑定与失败二者 SHA 均不同的新 corpus + Gold，且当前
   `final_blind` layer 状态为 `replaced`。未完成 replacement 时 verifier 拒绝 package。
8. 最后运行 verifier。该 manifest 的 `benchmark_only=true`、`claim_eligible=false`、
   `assembly_policy.assembly_enabled=false` 是不可变约束；不存在 release assembly 步骤。

## Artifact 和 result 约束

每个 artifact 需要唯一 `artifact_id`、mount/root、relative POSIX path、byte size、
file SHA、schema version、record SHA 和至少一个 `consumed_by`。verifier 重新读取 bytes，
拒绝 duplicate JSON key、nonfinite、绝对路径、`..`、symlink、越界文件、digest drift，
并要求所有 refs 闭包：manifest 声明的每个 artifact 必须在 package 结构或 lifecycle
event 中实际被消费。

三项 outcome 固定为 `continuity`、`wiki`、`legal`，每项同时绑定 raw output、scorer
source/executable、validator source/executable 和专属 Gate Result。Gate Result 的
candidate、protocol、threshold、classification、corpus role/source/frozen state 必须
与 package 完全一致。package 不复用其他 outcome 的 result，也不将 scorer 观察内容
嵌入新的评分器。

## Credential-free dry-run

本地 smoke check 可执行：

```bash
uv run --frozen python -m benchmarks.v013.product_outcome_package --dry-run
```

它只在临时目录创建明确标记为 synthetic development 的 corpus/Gold/结果 bytes，验证
三个 `prepared_not_executed` outcome、闭包、隔离 receipt 和现有 Gate Result envelope
validator；不读取 credentials/provider/network，不含 external/Human Gold、blind
holdout 或 pass result，不是 external evidence，不得据此宣称 qualification、release、
quality 或 competitive claim。
