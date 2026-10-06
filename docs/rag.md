# RAG learning map

本项目的 RAG 是一个本地、无 key、可审计的 retrieval-augmented generation
流程。它不是“把文档直接拼到 prompt”，而是把检索结果当成一种 typed evidence。
当前有三种引擎：`bm25`（零额外依赖）、`vector`（Sentence Transformers + Chroma）
和默认的 `hybrid`（两者用 RRF 合并）。这样既能学习真正的向量库，也不会因为模型
下载失败就让旅行 workflow 失效。

## 1. Ingest and chunk

`knowledge/*.md` 是知识源。`split_markdown()` 读取文件，
按 Markdown heading 和段落建立 `chunk_id`、`source`、`title`、
`section` 和 `text`。每个 chunk 不超过约 900 字符，避免单个文档独占上下文。

## 2. Lexical index (BM25)

`tokenize()` 同时处理 Latin、中文和日文片段。初始化索引时，retriever 保存每个
chunk 的 token 频次和 document frequency。

## 3. Retrieve

`MarkdownKnowledgeBase.search()` 使用 BM25 计算相关性。它有三个硬边界：

- 最多返回 `knowledge_top_k` 个 chunk，默认 5 个；
- 最多使用 `knowledge_max_chars` 个字符，默认 4500；
- 按分数、source、chunk id 确定性排序。

因此知识库扩大后，Planner prompt 仍然有可预测的上下文预算。

## 3. Semantic index (embedding + Chroma)

可选的 `rag-vector` extra 使用 `intfloat/multilingual-e5-small`。它适合中文、英文
和旅行地点混合查询，模型卡要求检索查询使用 `query:` 前缀、知识段落使用
`passage:` 前缀，代码在 `SentenceTransformerEmbedder` 中统一添加。模型首次运行会
从 Hugging Face 下载到本机缓存；不需要 API key，也不需要 GPU，小知识库用 CPU 足够。

`ChromaKnowledgeBase` 用 `PersistentClient` 把向量、原文和 source/section metadata
保存在 `--rag-db-dir`，默认 `.local/chroma_rag`。它用文档内容哈希判断索引是否需要重建，
所以第二次运行不会无条件重新计算 embedding。Chroma 的距离是 cosine distance，代码
把它转换成 `score = 1 - distance`，再保持原有 `KnowledgeHit` 接口。

## 4. Hybrid ranking

`HybridKnowledgeBase` 不直接比较 BM25 分数和 cosine 分数，因为两个分数的量纲不同。
它分别取候选结果，再用 Reciprocal Rank Fusion：第 `rank` 名贡献 `1 / (60 + rank)`。
一个词完全不同但语义相近的查询可以由 embedding 找到，明确点名的词仍可由 BM25 保证，
最后的 source、section、chunk 和最大字符数仍由程序控制。

## 5. Plan and execute

`build_search_plan()` 根据结构化目的地、偏好、偏好查询词和服务范围生成
`knowledge_query`。`execute_search_plan()` 只有在 CLI 配置了知识库时才记录
`retrieve_knowledge`，结果封装为 `ToolResult[list[KnowledgeHit]]`。

## 6. Ground the model

`build_planner_prompt()` 同时传入原始输入、`TripRequest`、`SearchPlan`、工具结果、
检索结果和长期偏好。prompt 明确要求：知识库只支持一般性说明，不能变成实时价格、
营业时间、库存或政策结论。

## 7. Preserve citations

`_apply_request_constraints()` 从成功的 `KnowledgeHit` 生成
`TripPlan.knowledge_sources`。Planner 可以写自然语言，但不能新增或修改 citation。
`render_trip_plan()` 在“知识参考”中显示 source、section 和 score。

## 8. Evaluate

`quality.check_workflow_invariants()` 检查 `knowledge_sources` 是否完全来自检索结果。
`tests/test_rag.py` 测试中英文 token、section 保留、query 构造、调用记录和上下文边界；
`evals/evaluate_workflow.py` 的 `rag_evidence_is_preserved` case 测试 workflow 不会丢掉
RAG evidence。

## 9. Run it

```bash
# 只用无额外依赖的 BM25
PYTHONPATH=src uv run python -m travel_agent.app.cli --rag-engine bm25 --debug

# 安装本地 embedding 和 Chroma（首次可能下载模型）
uv sync --extra dev --extra rag-vector
PYTHONPATH=src uv run python -m travel_agent.app.cli \
  --rag-engine hybrid --rag-model intfloat/multilingual-e5-small \
  --rag-db-dir .local/chroma_rag --debug

# 强制只用向量检索；依赖/模型不可用时会明确失败，不会伪装成 BM25
PYTHONPATH=src uv run python -m travel_agent.app.cli --rag-engine vector
```

如果 `hybrid` 初始化向量依赖失败，它会返回 BM25 retriever。这个降级只发生在检索
引擎构建阶段；工具调用记录仍然会显示实际 provider，最终输出不会声称使用了向量结果。
`--no-rag` 仍然可以完全关闭知识检索。

## 10. What this RAG deliberately does not claim

- 本地知识库不是实时数据源；
- embedding 只表达文本相似性，不验证实时事实；
- BM25 是 lexical retrieval，vector/hybrid 才使用 semantic search；
- citation 证明“来自哪个 chunk”，不证明内容永远正确；
- 旅行天气、地点、路线仍然必须来自对应工具；
- MCP 只是可选的协议入口，不会改变 RAG 的证据边界。
