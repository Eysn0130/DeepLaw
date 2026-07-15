# DeepLaw 2.0 Agent 知识库研究矩阵

Status: research decision record, reviewed 2026-07-16. This document is not a
benchmark result or a claim of cross-system superiority.

## 结论

DeepLaw 2.0 的核心不应是把更多检索、图谱、Wiki、Agent 或求解器堆进同一条路径，而是保持：

```text
Discovery != Admission != Selection != Adjudication
```

1. **Canonical / Admission**：不可变原件、hash、官方来源、发布主体、辖区、公布/施行/失效
   区间、版本链、抽取 provenance/risk、签名目录和人工复核状态。
2. **Discovery sidecars**：词法、向量、融合、重排、树、图、Wiki 和 Agent 只能提出候选 ID/span。
3. **Derived reasoning sidecars**：本体、规则、Datalog、SMT 或多 Agent 只在已准入证据和显式假设
   上运行，输出必须标 `derived` 并绑定输入、规则版本和 proof/argument trace。
4. **Bounded Evidence Compiler**：在卡片和字符预算内按证据义务选择，返回 evidence、uncertain、
   gaps 与 receipts。案件事实认定、规则适用和裁判不属于 DeepLaw。
5. **Source-bound Topic Gate**：已审核法律概念只通过原件 hash + 条款 locator 或可见的精确主题
   边界进入证据编译；相邻罪名、复合概念和其他标准不得补位，无法解析即 blocking gap。

任何 sidecar 都不得写入或提升 authority、effective interval、version lineage、review status 或
evidence status。派生内容可删除、可重建，且必须回到 immutable source/span。

## 技术矩阵

| 方向 | 可验证优势 | 主要短板 | DeepLaw 决策 |
| --- | --- | --- | --- |
| 向量检索 | 同义表达与事实模式召回 | 相似度不证明来源、版本、效力或适用；OOD 无通用赢家 | 可选 Discovery sidecar |
| Reranker | 在首阶段候选内提高相关性排序 | 无法找回首阶段漏项；分数仍只是相关性 | 只重排 candidate IDs |
| Tree / PageIndex | 长文档层级导航、跨章节定位 | 摘要有损且模型依赖 | 只导航，答案回落 leaf/span |
| Graph-based retrieval | 跨文档专题与 global sensemaking | LLM 实体、关系、claim 和 community summary 可能错误且索引昂贵 | 派生图隔离；权威图只收确定性来源关系 |
| LLM Wiki | 适合人读的概念页、专题导航和 Obsidian 视图 | 重编译会改写内容；词条不能表达全部页级 provenance/风险 | 只作可重建 Markdown/Explain 视图 |
| Hybrid search | 词法与语义互补 | fusion 策略无通用赢家，仍不产生权威性 | 可选 Discovery sidecar |
| Ontology / KAG | 类型、约束、多跳和 schema 对齐 | LLM OpenIE 错误传播，领域 schema 成本高 | 人工复核本体可约束；LLM 变更仅提案 |
| Agent retrieval | 查询分解与迭代探索 | 级联幻觉、memory poisoning、工具风险和预算漂移 | 受预算 controller 约束，留在 Admission 之外 |
| Datalog / neuro-symbolic | 对显式 facts/rules 做可追溯推导 | 只证明相对于编码输入的推导，不证明法律编码忠实 | 仅 derived reasoning sidecar |
| LawThinker | Explore/Verify 分离有助于强制检查 | verifier 仍含同模型判断和向量 top-1 模糊匹配；无官方来源/版本硬门禁 | 借 controller 原则，不复用其准入语义 |
| ACAL | 控辩 argument graph 提高可争辩性 | 论点、边与分数由 LLM 产生；公开仓库无 LICENSE | 只可在已准入证据后生成 derived 论证图 |
| 动态本体 | 语义、时间、审计和高风险人工门禁分层 | 缺少公开可复现实验与开源实现 | 借分层，不引入 action execution |
| SMT / Z3 | 对给定形式化公式做确定性 satisfiability/entailment | 证明的是编码；LLM 形式化会 scope laundering、漏隐含约束或生成错误程序 | 仅限人工复核窄域，输出永不冒充法源或裁判 |

## 法律标准优先于自造 schema

后续法律层级与双时态事件账本应优先映射成熟概念，而不是直接复制完整 XML：

- [Akoma Ntoso 1.0](https://www.oasis-open.org/standard/akn-v1-0/)：正式法律文本结构、生命周期、
  修改、时间与编辑元数据；
- [LegalRuleML 1.0](https://www.oasis-open.org/standard/legalrulemlv1-0/)：规则、权威、辖区、时间与
  provenance；
- [OWL 2](https://www.w3.org/TR/owl2-syntax/) 与
  [SHACL](https://www.w3.org/TR/shacl/)：开放世界语义和确定性约束验证。

缺失事实在开放世界中不等于 false；本体或规则验证通过也不自动产生法律适用结论。

## 文档解析研究结论

没有单一解析库能替代“原件 + 多候选 + 独立门禁 + Document IR”。当前默认结构化候选保留，
困难页未来可增加 [PaddleOCR / PP-StructureV3](https://github.com/PaddlePaddle/PaddleOCR) 或高精度
VLM 候选，但任何模型输出仍需独立挑战。公开基准如
[OmniDocBench](https://github.com/opendatalab/OmniDocBench) 测平均解析质量，不测法条号、否定词、
金额期限、错误证据准入或版本效力，不能作为法律主证据门禁。

规范存储仍是：

```text
immutable original bytes + SHA-256
  -> layout-aware Document IR / SQLite
  -> rebuildable Markdown / search / tree / graph / wiki views
```

Markdown 是人读视图，不是 canonical truth。历史 `.doc` 只有在同时保留原 DOC、转换后 DOCX、
双 hash、转换器版本和日志后才可支持；当前拒绝静默转换。

## 公开对照门禁

当前 28 份资料、37 项白盒回归（含 4 项主题错召回负例）不能证明 DeepLaw 超过其他系统。对外
领先结论至少需要：

- 冻结同一官方语料、版本/time slices、OCR 扰动、问题和上下文/模型/成本预算；
- held-out 精确条款、主题导航、事实模式、多跳引用、修订废止、错误版本、反证、无答案、
  abstention 与 source-swamp；
- 同时运行 BM25、dense、hybrid、reranker、tree、graph、wiki-style 与 agentic baselines；
- 盲法学专家裁决，并公开配置、raw outputs、失败样本和置信区间；
- 指标覆盖 admitted-evidence precision/recall、wrong-version、invalid-authority admission、
  uncertain leakage、citation/span correctness、counterevidence、abstention、Token、延迟、成本与
  replay consistency。

通过前只能声明 DeepLaw 具有可验证的架构属性，不能使用 “world strongest”、 “surpasses all”
或跨方法 SOTA 表述。

## 主要一手资料

- [RAG](https://papers.neurips.cc/paper/2020/file/6b493230205f780e1bc26945df7481e5-Paper.pdf),
  [BEIR](https://github.com/beir-cellar/beir),
  [SentenceTransformers retrieve-rerank](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)
- [RAPTOR](https://openreview.net/forum?id=GN921JHCRw),
  [PageIndex](https://github.com/VectifyAI/PageIndex),
  [Microsoft GraphRAG](https://github.com/microsoft/graphrag)
- [gbrain](https://github.com/garrytan/gbrain),
  [OpenKB](https://github.com/VectifyAI/OpenKB),
  [OpenSearch hybrid search](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/)
- [OpenSPG/KAG](https://github.com/OpenSPG/KAG),
  [Souffle](https://github.com/souffle-lang/souffle),
  [Scallop](https://github.com/scallop-lang/scallop)
- [LawThinker](https://github.com/RUC-NLPIR/LawThinker-agent),
  [ACAL](https://github.com/loc110504/ACAL),
  [Z3](https://github.com/Z3Prover/z3),
  [Catala](https://github.com/CatalaLang/catala)
