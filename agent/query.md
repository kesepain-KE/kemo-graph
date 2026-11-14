# 检索与问答

[返回操作入口](README.md)

四种检索模式互相独立，按证据类型选择，不要一次全跑。

| 模式 | 端点 | 适合 |
| --- | --- | --- |
| 图谱查询 | `POST /api/v1/query/graph` | 关键词/实体命中、关系扩展、节点群总结 |
| 向量查询 | `POST /api/v1/query/rag` | 文档切片语义召回与重排序 |
| 混合查询 | `POST /api/v1/query/hybrid` | 先图谱命中并增强关联切片，再向量召回 |
| 全局查询 | `POST /api/v1/query/global` | 先召回相关节点群，再结合群总结与关键实体生成整体回答 |

需要模型组织成自然语言答案时用 `POST /api/v1/query/answer`，它在混合检索之后把只读证据交给模型。

四种查询都接受可选查询参数 `force=true`：跳过已有缓存重新执行，成功结果仍会写回缓存。默认 CLI、API 与网页共享 `data/search_cache.db`。

## 图谱查询

```python
response = client.post("/api/v1/query/graph", json={
    "query": "知识图谱如何增强向量检索",
    "depth": 3,
    "direction": "both",
    "confidence": 0.7,
})
data = response.json()["data"]
```

| 字段 | 必填 | 约束 |
| --- | :---: | --- |
| `query` | 是 | 非空 |
| `depth` | 否 | 1–10，默认 3，单方向扩展深度 |
| `direction` | 否 | `forward` / `backward` / `both`，默认 `both` |
| `confidence` | 否 | 0–1，空值用配置默认 |

`data` 包含 `hit_nodes`（直接匹配）、`expanded_nodes`（BFS 扩展）、`edges`、`relations`（一跳）、`paths`（多跳）、`groups`（关联节点群总结）。

注意 `direction="both"` 会同时做正向与反向扩展，**不要把 `depth=3` 理解成只返回三个节点**。`paths` 每项含 `text`、`node_ids`、`edge_ids`、`weight`（链上最小关系权重）、`depth`，由本地 BFS 最短树确定性生成。

## 向量（RAG）查询

```python
response = client.post("/api/v1/query/rag", json={
    "query": "如何导入 PDF 文档",
    "top_k": 10,
    "threshold": 0.6,
})
```

| 字段 | 必填 | 约束 |
| --- | :---: | --- |
| `query` | 是 | 非空 |
| `top_k` | 否 | 1–100，空值用配置默认 |
| `threshold` | 否 | 0–1，空值用 `rag_similarity_threshold` |

处理链路：查询规划（可选 LLM 改写与子问题）→ 批量生成查询向量 → FAISS 多路召回 + 加权 RRF → 精确词面候选补充 → 分层切片家族折叠并补父级上下文 → Rerank → 阈值过滤。

每条 `data.results` 项含 `chunk_id`、`content`、`score`、`granularity`（small/medium/large）、`parent_chunk_id`、`context`、`source: {source_id, relative_path}`。

**引用给用户时带上 `source.relative_path`**，这是能回溯到原文的关键。

查询规划失败会退化为原始问题，不会让检索整体失败。

## 混合查询

```python
response = client.post("/api/v1/query/hybrid", json={
    "query": "知识图谱和向量检索的关系",
    "graph_depth": 3,
    "rag_top_k": 10,
    "graph_confidence": 0.7,
    "rag_threshold": 0.6,
})
data = response.json()["data"]
```

`data` 含四路结果：`graph`、`rag`、`entities`、`communities`。Graph 与 RAG 结果**不做去重**——一个是结构关系，一个是文档片段。

实体与群组是补充上下文，分数分别乘 `vector_search.entity_weight` 与 `community_weight`，不会改写原有 `graph`/`rag` 结构。

## 混合检索问答

```python
response = client.post("/api/v1/query/answer", json={
    "query": "这个项目为什么选择本地优先",
})
data = response.json()["data"]
answer = data["answer"]        # 带来源说明的知识库回答
evidence = data["retrieval"]   # 完整混合检索结果，供审计
```

请求字段与混合查询相同。**模型只能依据只读检索证据回答**。若没有检索证据，接口不调用模型，直接返回证据不足提示。

审计答案时看 `retrieval`，不要只信 `answer`。

## 全局查询

```python
response = client.post("/api/v1/query/global", json={
    "query": "知识库中主要讨论了哪些技术方向？",
    "top_k": 5,
})
```

`top_k` 为参与回答的最大相关群组数（1–100）。系统先检索群组总结向量，再加载成员，并选取引用次数最高的 10 个关键实体作为受约束上下文。

`data` 含 `query`、`answer`、`communities`、`key_entities`。

**前置条件**：若尚未生成节点群总结，接口不会调用 LLM，返回空的 `communities`、`key_entities` 和“请先生成节点群总结”的提示。此时应先用 `POST /api/v1/maintenance/organize-graph`。

## 检索进度

`GET /api/v1/query/progress/{progress_id}` 返回本次检索实际执行的步骤。未执行的步骤不会显示为完成；缓存命中与规划回退有独立标记。

实时步骤是进程内短期状态，多进程部署需把查询与进度轮询路由到同一工作进程。

## 搜索缓存与历史

```python
client.get("/api/v1/search/cache", params={"page": 1, "page_size": 20})
client.get("/api/v1/search/cache/{cache_key}")
client.delete("/api/v1/search/cache", params={"stale_only": True})  # 只删过期
client.delete("/api/v1/search/cache")                                # 清空全部
```

列表按最近命中或更新时间倒序。每条含 `cache_key`、`query_mode`、`query`、`params`、`result_size`、`is_stale`、`hit_count`、时间戳。详情端点额外返回 `result`，结构与原查询端点的 `data` 完全一致；**读取历史详情不会增加 `hit_count`**。

缓存是透明加速层：缓存库损坏或不可写时查询会降级为原始检索，不会阻断知识查询。
