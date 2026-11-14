# kemo-graph 外部智能体 API

> **用途**：本文件定义 kemo-graph 对外提供给 kemo-agent、其他智能体或自动化程序的 HTTP API。
> **不包括**：Web 前端页面、React 路由、浏览器交互约定。
> **当前版本**：`2.0.0`
> **实现来源**：`api/__init__.py`、`api/routes.py`、`api/store_routes.py`、`api/library_routes.py`、`api/schemas.py`。

---

## 1. 服务与调用边界

### 1.1 独立启动

外部 API 可以不启动 Web 前端，直接启动独立 FastAPI 应用：

```powershell
cd <kemo-graph-install-dir>
uvicorn api:app --host 127.0.0.1 --port 8000
```

默认 API 根路径：

```text
http://127.0.0.1:8000/api/v1
```

也可以使用 `python start_web.py` 启动；该方式会额外挂载网页前端，但 API 路径保持相同。

### 1.2 鉴权与部署安全

**当前 API 未实现应用层鉴权。** 因此默认只应监听本机回环地址：

```text
127.0.0.1
```

不要直接将该端口暴露到公网或不可信局域网。删除、导入、配置和维护端点均可修改本地知识库数据。

### 1.3 数据与操作原则

- 所有 JSON 请求的 `Content-Type` 为 `application/json`。
- 文档导入端点使用 `multipart/form-data`。
- 所有 JSON 请求模型拒绝未声明字段；不要传入额外参数。
- 图谱或 RAG 正在构建时，依赖该数据的一些读取/修改请求会返回 `409 PROCESSING`，调用方应等待后重试。
- 删除操作具有副作用，智能体执行前应先查询、确认目标 ID 与影响范围。
- 节点和关系删除均有公开 HTTP API；删除前应先调用详情端点核对来源和影响范围。

### 1.4 部署形态差异

- `uvicorn api:app` 启动纯 API 服务，不挂载网页前端，也不注册 `/api/v1/system/runtime` 与 `/api/v1/system/restart`。
- `python start_web.py` 启动网页与同一组 API，并额外注册上述运行状态与底层重启端点。

外部智能体若只依赖知识库 API，优先使用纯 API 形态；需要网页控制台、运行状态或网页重启能力时使用 `start_web.py`。

### 1.5 端点可见性

`GET /api/v1/graph/full` 是兼容旧调用方的别名，设置了 `include_in_schema=False`，因此不会出现在 OpenAPI `paths` 中。新调用方应使用 `GET /api/v1/graph`；不能仅根据 OpenAPI 缺少该兼容别名就判断服务端未实现完整图谱读取。

---

## 2. 通用响应格式

所有端点均返回以下包络：

```json
{
  "ok": true,
  "data": {},
  "error": null
}
```

失败时：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "INVALID_PARAM",
    "message": "具体错误说明"
  }
}
```

### 2.1 常见错误码

| HTTP | error.code | 含义 | 调用方建议 |
|---:|---|---|---|
| 400 / 422 | `INVALID_PARAM` | 参数、请求体或业务前置条件非法 | 修正参数后再试 |
| 400 | `INVALID_PATH` | 导入路径不安全、不是可读取的绝对文件路径，或上传文件名包含目录 | 使用安全文件名或修正本地文件路径 |
| 404 | `NOT_FOUND` | source_id、node_id 或路由不存在 | 先刷新状态/图谱确认 ID |
| 409 | `PROCESSING` | 知识库正在整理 | 等待后重试，不并发修改 |
| 409 | `IMPORT_SOURCE_CHANGED` | 源文件与调用方确认的 SHA-256 不一致，或捕获期间发生变化 | 重新扫描文件后再发起导入 |
| 413 | `FILE_TOO_LARGE` | 导入文件超过 50 MB，或转换结果超过 20,000,000 字符 | 拆分或精简内容 |
| 415 | `UNSUPPORTED_FORMAT` | 扩展名或内容识别结果没有对应 Converter | 先转换为支持格式 |
| 422 | `CONVERSION_FAILED` | 文档无法转换为 Markdown，或缺少对应格式依赖 | 检查文档及 message 中的依赖名 |
| 422 | `IMPORT_FAILED` | 导入过程失败 | 查看 message 与服务日志 |
| 502 | `INGEST_FAILED` | 转换成功，但图谱/RAG 整理失败 | 文档已保留，可稍后调用 ingest 重试 |
| 503 | `NOT_INITIALIZED` | 知识库尚未初始化 | 先导入或扫描至少一篇文档 |
| 503 | `SEARCH_CACHE_CLEANUP_FAILED` | 数据变更已生效，但包含旧正文的搜索缓存未能清理 | 刷新数据列表，并单独调用搜索缓存清理接口；不要重放已完成的变更 |
| 403 | `STORE_ACCESS_DENIED` | Store 路径不符合绝对路径或 allowlist 边界 | 修正路径或 portable_stores 配置 |
| 404 | `STORE_NOT_INITIALIZED` | 目标位置没有有效 manifest | 先调用 stores/initialize |
| 422 | `STORE_INVALID` | Store 清单、scope、owner 或目录状态非法 | 检查 kemo-graph-storage/manifest.json |
| 500 | `INTERNAL` | 未预期内部错误 | 查看 `log/YYYY-MM-DD.tsv` |

---

## 3. 读取状态与图谱

### 3.1 获取知识库状态

```http
GET /api/v1/status
```

用于智能体在执行导入、查询、删除前确认初始化状态、文档数量、Graph/RAG 状态及 FAISS 健康状态。

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/status
```

返回 `data` 包含知识库概览；调用方应以实际响应字段为准，不应假设空库已存在 Graph 或 RAG 索引。

---

### 3.2 获取完整图谱

```http
GET /api/v1/graph
GET /api/v1/graph?nodes_page=1&nodes_page_size=100
```

不传分页参数时返回当前全部节点；传入 `nodes_page` 后仅对节点分页，关系线和节点群总结仍始终全量返回。适合智能体需要：

- 找到 `node_id` / `edge_id`；
- 审查可删除目标；
- 在本地做关系分析；
- 查看节点群总结。

成功响应示例：

```json
{
  "ok": true,
  "data": {
    "nodes": [
      {
        "node_id": "node-uuid",
        "keyword": "知识图谱",
        "summary": "……",
        "aliases": ["KG"],
        "tags": ["AI"],
        "ref_count": 2,
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00"
      }
    ],
    "edges": [
      {
        "edge_id": "edge-uuid",
        "source_node_id": "node-a",
        "relation": "包含",
        "target_node_id": "node-b",
        "weight": 0.9,
        "support_count": 1,
        "created_at": "2026-08-01T00:00:00+00:00"
      }
    ],
    "groups": [
      {
        "group_id": "group-uuid",
        "summary": "……",
        "node_count": 3,
        "edge_count": 2,
        "node_ids": ["node-a", "node-b", "node-c"]
      }
    ],
    "nodes_pagination": {
      "page": null,
      "page_size": 1,
      "total": 1,
      "total_pages": 1
    }
  },
  "error": null
}
```

兼容别名：`GET /api/v1/graph/full`。

### 3.3 GPU 可视化分页

Phase 14A 新增可视化专用数据契约，节点和关系分别分页，避免节点翻页时重复传输全部关系：

```http
GET /api/v1/graph/visualization/meta
GET /api/v1/graph/visualization/nodes?page=1&page_size=1000&expected_revision={revision}
GET /api/v1/graph/visualization/edges?page=1&page_size=2000&expected_revision={revision}
```

`meta` 返回当前内容驱动的 64 位 `revision`、节点/关系/群组数量和群组摘要。节点页额外返回每个节点的 `source_ids` 与 `group_ids`；关系页只返回关系。所有分页响应都携带相同的 `revision`。

客户端应先读取 `meta`，随后把该值作为 `expected_revision` 请求各页。加载期间图谱发生变化时，服务端返回 HTTP 409：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "GRAPH_CHANGED",
    "message": "图谱已发生变化，请重新加载……"
  }
}
```

旧的 `GET /api/v1/graph` 行为保持不变。

### 3.4 确定性局部图谱

```http
GET /api/v1/graph/neighborhood/{node_id}?depth=2&direction=both&limit=2000&edge_limit=10000&expected_revision={revision}
```

- `depth`：1～6；
- `direction`：`forward`、`backward` 或 `both`；
- `limit`：节点上限，最大 10000；
- `edge_limit`：内部关系上限，最大 50000；
- `expected_revision`：可选的快照一致性保护。

该端点直接按 `node_id` 执行数据库 BFS，不调用 LLM、Embedding 或 Rerank。响应中的节点包含 `depth`，并用 `truncated` / `edges_truncated` 表示是否达到安全上限。

---

## 4. 查询 API

四种查询模式互相独立：

| 模式 | 端点 | 用途 |
|---|---|---|
| 图谱查询 | `POST /query/graph` | 关键词/实体命中、关系扩展、群总结 |
| 向量查询 | `POST /query/rag` | 文档切片语义召回与重排序 |
| 混合查询 | `POST /query/hybrid` | 先图谱命中并增强关联切片，再执行向量召回和重排序 |
| 全局查询 | `POST /query/global` | 先召回相关节点群，再结合群总结和关键实体生成整体回答 |

四种查询都接受可选查询参数 `force=true`。启用时跳过已有缓存并重新执行完整检索，成功结果仍会更新缓存。默认情况下，CLI、API 与网页端共享 `data/search_cache.db` 中的结果；知识状态或有效配置变化后旧记录不会参与查询。

### 4.1 图谱查询

```http
POST /api/v1/query/graph
Content-Type: application/json
```

请求体：

```json
{
  "query": "知识图谱如何增强向量检索",
  "depth": 3,
  "direction": "both",
  "confidence": 0.7
}
```

字段：

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|:---:|---|---|
| `query` | string | 是 | 非空 | 用户问题或概念描述 |
| `depth` | integer | 否 | 1–10，默认 3 | 单方向关系扩展深度 |
| `direction` | string | 否 | `forward` / `backward` / `both`，默认 `both` | 关系遍历方向 |
| `confidence` | number/null | 否 | 0–1 | 实体/节点匹配置信度阈值；空值使用配置默认值 |

`direction="both"` 会同时执行正向和反向扩展；调用方不应把 `depth=3` 理解成只返回三个节点。

响应 `data` 包含：

```text
query
hit_nodes        # 直接匹配的节点
expanded_nodes   # BFS 扩展得到的节点
edges            # 涉及的关系线
relations        # 一跳：A->[关系]->B
paths            # 多跳：A->[关系]->B->[关系]->C
groups           # 关联节点群总结
```

`relations` 每项保留完整 edge 字段并增加 `text`。`paths` 每项包含：

```json
{
  "text": "知识图谱->[增强]->RAG->[使用]->Embedding",
  "node_ids": ["node-a", "node-b", "node-c"],
  "edge_ids": ["edge-ab", "edge-bc"],
  "weight": 0.84,
  "depth": 2
}
```

多跳路径由本地 BFS 最短树确定性生成，权重取链上最小关系权重，数量受 `graph_path_limit` 控制。关系方向不会为了排版被改写。

---

### 4.2 向量（RAG）查询

```http
POST /api/v1/query/rag
Content-Type: application/json
```

请求体：

```json
{
  "query": "如何导入 PDF 文档",
  "top_k": 10,
  "threshold": 0.6
}
```

字段：

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|:---:|---|---|
| `query` | string | 是 | 非空 | 查询文本 |
| `top_k` | integer/null | 否 | 1–100 | 最多返回多少个重排序结果；空值使用配置默认值 |
| `threshold` | number/null | 否 | 0–1 | 最终分数阈值；空值使用 `rag_similarity_threshold` |

处理流程：

```text
原始问题
  → 查询规划（可选 LLM 改写、同义表达、相关概念与子问题）
  → 一次批量生成全部查询向量，并过滤语义漂移扩展
  → FAISS 多路召回 + 加权 RRF 融合
  → 有界精确词面候选补充（中文短词、标识符等）
  → 分层切片家族折叠并补充父级上下文
  → Rerank → 阈值过滤；仅在默认阈值、扩展查询且无结果时使用受限低分兜底
```

查询规划失败时会退化为原始问题，不会让检索整体失败。Graph、RAG 与 Hybrid
共享同一个已规划查询；Hybrid 的 chunk、实体摘要与节点群向量检索也复用同一批
查询向量，不会为每一路重复请求 Embedding。

响应 `data.results` 中每项包含：

```text
chunk_id
content
score
granularity       # small / medium / large
parent_chunk_id
context           # 父级上下文或 null
source: { source_id, relative_path }
```

---

### 4.3 混合查询

```http
POST /api/v1/query/hybrid
Content-Type: application/json
```

请求体：

```json
{
  "query": "知识图谱和向量检索的关系",
  "graph_depth": 3,
  "rag_top_k": 10,
  "graph_confidence": 0.7,
  "rag_threshold": 0.6
}
```

字段：

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|:---:|---|---|
| `query` | string | 是 | 非空 | 查询文本 |
| `graph_depth` | integer | 否 | 1–10，默认 3 | 图谱扩展深度 |
| `rag_top_k` | integer/null | 否 | 1–100 | RAG 返回数量 |
| `graph_confidence` | number/null | 否 | 0–1 | 图谱节点匹配阈值 |
| `rag_threshold` | number/null | 否 | 0–1 | RAG 结果阈值 |

固定流程：

```text
图谱查询
  → 命中节点和扩展节点
  → chunk_nodes 找到关联文档切片
  → 给这些切片施加 configured hybrid_enhancement_factor
  → Embedding / FAISS / Rerank
  → 同时检索实体摘要和群组总结向量
  → 分别返回 graph、rag、entities 与 communities
```

响应：

```json
{
  "ok": true,
  "data": {
    "graph": { "hit_nodes": [], "expanded_nodes": [], "edges": [], "relations": [], "paths": [], "groups": [] },
    "rag": { "query": "……", "results": [] },
    "entities": [{ "node_id": "…", "keyword": "…", "summary": "…", "score": 0.8, "source": "entity" }],
    "communities": [{ "group_id": "…", "summary": "…", "node_count": 8, "score": 0.6, "source": "community" }]
  },
  "error": null
}
```

Graph 与 RAG 结果不去重，因为一个是结构关系，另一个是文档片段。

实体和群组分数分别乘以 `vector_search.entity_weight` 与
`vector_search.community_weight`。这两类结果是补充上下文，不会改写旧的
`graph` / `rag` 响应结构。

---

### 4.4 混合检索问答

```http
POST /api/v1/query/answer
Content-Type: application/json
```

请求字段与混合查询相同。系统先完成混合检索，再把图谱关系链、节点摘要、RAG 原文片段、实体与群组上下文交给 LLM；模型只能依据这些只读检索证据回答。

响应 `data`：

```text
query
answer       # 带来源说明的知识库回答
retrieval    # 完整混合检索结果，供调用方审计答案依据
```

若没有检索证据，接口不调用模型，直接返回证据不足提示。

---

### 4.5 全局查询

```http
POST /api/v1/query/global
Content-Type: application/json
```

请求体：

```json
{
  "query": "知识库中主要讨论了哪些技术方向？",
  "top_k": 5
}
```

`top_k` 允许 1–100，表示参与回答的最大相关群组数。系统先检索群组总结向量，
再加载这些群组的成员，并选取引用次数最高的 10 个关键实体作为受约束上下文。

响应 `data` 包含：

```text
query
answer          # LLM 基于选中群组与实体生成的回答
communities     # 分数、群组统计和 node_ids
key_entities    # 关键节点、权重、引用次数及所属群组
```

若尚未生成节点群总结，接口不会调用 LLM，而是返回空的 `communities`、
`key_entities` 和“请先生成节点群总结”的提示。

---

### 4.6 搜索缓存与历史

```http
GET    /api/v1/search/cache?page=1&page_size=20
GET    /api/v1/search/cache/{cache_key}
DELETE /api/v1/search/cache?stale_only=true
DELETE /api/v1/search/cache
```

列表按最近命中或更新时间倒序返回。每条记录包含：

```text
cache_key
query_mode       # graph / rag / hybrid / global
query
normalized_query
params           # 已解析的有效查询参数
result_size      # UTF-8 JSON 字节数
state_hash
is_stale         # 是否与当前知识库状态不一致
hit_count        # 真正从缓存返回的次数
created_at / updated_at / last_hit_at
```

详情端点额外返回 `result`，其结构与原查询端点的 `data` 完全一致。读取历史详情不会增加 `hit_count`。

`stale_only=true` 只删除过期记录；不传时清空全部缓存。删除文档或节点时，服务端会主动清空缓存结果，避免已删除正文继续保留在历史结果中。

文档删除、批量删除、节点／关系删除和源同步的数据变更完成后，如果清理失败，
返回 `503 SEARCH_CACHE_CLEANUP_FAILED`，不会把未知清理数量伪报成 `0`。
响应仍使用 `ok=false, data=null`，`error.result` 保留已完成的变更结果，
其中 `search_cache_deleted=null`、`search_cache_error` 为具体清理错误。
全部删除跨多个批次时，结果还包含已处理批次的累计计数及 `unprocessed` 数量，
首次清理失败后不再处理后续批次。调用方应刷新状态，并单独调用
`DELETE /api/v1/search/cache`，不要重复提交已经完成的删除或同步。

缓存是透明加速层。缓存数据库损坏或暂时不可写时，查询会降级执行原始检索，不会因缓存故障阻断知识查询。

---

## 5. 文档 API

### 5.1 列出文档

```http
GET /api/v1/documents
GET /api/v1/documents?status=active
GET /api/v1/documents?status=pending
GET /api/v1/documents?status=all
GET /api/v1/documents?status=active&page=1&page_size=20
GET /api/v1/documents?status=active&page=1&page_size=6&directory=研究资料&search=画布&graph_status=ready&rag_status=ready&include_summary=true
```

返回：

```json
{
  "ok": true,
  "data": {
    "documents": [
      {
        "source_id": "source-uuid",
        "relative_path": "example-a1b2c3.md",
        "source_path": "研究资料/example.pdf",
        "original_path": "E:/kemo-graph/external/originals/研究资料/example.pdf",
        "content_hash": "sha256…",
        "graph_status": "ready",
        "rag_status": "ready",
        "exists_status": "active",
        "created_at": "…",
        "updated_at": "…"
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 1,
      "total_pages": 1
    },
    "summary": {
      "total_active": 12,
      "pending_documents": 2,
      "needs_rebuild_documents": 3,
      "graph": {"pending": 1, "processing": 0, "ready": 10, "failed": 1},
      "rag": {"pending": 1, "processing": 0, "ready": 10, "failed": 1}
    }
  },
  "error": null
}
```

建议智能体删除或读取内容前，先调用本端点取得 `source_id`。

可选筛选参数：

- `directory`：原文件相对目录；空字符串表示根目录中的文件，非空目录包含其子目录，省略表示全库；
- `search`：按原文件路径做不区分大小写的字面子串搜索，`%` 和 `_` 不作为通配符；
- `graph_status` / `rag_status`：`pending`、`processing`、`ready`、`failed`；
- `include_summary=true`：附加当前活动知识库的 Graph/RAG 汇总统计，不改变 `documents` 分页内容。

`summary.needs_rebuild_documents` 表示 Graph 或 RAG 为 `pending/failed` 的活动文档数，可用于显示批量重建提示。默认不返回 `summary`，以保持旧客户端响应大小和兼容性。

每篇文档的 `builds` 保存各链路最近一次实际执行的步骤，没有记录时为 `{}`。`graph` / `rag` 条目包含该次构建的 `content_hash` 和按执行顺序排列的 `steps`；每步包含 `step`、`label`、`status`（`processing` / `completed` / `failed`）、`error`、`started_at`、`finished_at`。只记录真正开始或完成的步骤，不根据最终状态反推历史。失败后保留此前成功步骤与具体错误；服务中断时，未完成步骤会标记为失败。处理稿发生变化时保留上次步骤及其哈希，供客户端明确区分历史构建。

### 5.1.1 原文件目录、重命名与移动

原文件保存在 Markdown 根目录同级的 `originals/` 中，默认是 `external/originals/`。知识库目录直接映射这里的实际文件与文件夹，无共享、用户或项目分类。规范 Markdown 单独保存在 `external/markdown/`，通过 `sources.library_path`、稳定的 `source_id` 与 `file_map.json` 关联。文档列表的 `source_path` 是原文件相对路径，`relative_path` 是处理产物相对路径。

| 方法与端点 | 用途 | 请求体 / 参数 |
| --- | --- | --- |
| `GET /api/v1/library` | 目录树与当前目录第一层内容 | 查询参数 `directory`、`search`，均默认为空 |
| `POST /api/v1/library/entries` | 在已有目录新建文件夹或文档 | `{"directory":"研究资料","name":"笔记.md","kind":"file"}`；文件夹用 `kind:"directory"` |
| `PATCH /api/v1/library/entries` | 移动或重命名实际文件/文件夹 | `{"path":"研究资料/笔记.md","destination":"归档/笔记.md"}` |
| `POST /api/v1/library/transfer` | 将原文件或整个目录复制、剪切到目标目录 | `{"path":"研究资料/笔记.md","directory":"归档","mode":"copy","conflict":"error"}`；剪切用 `mode:"cut"`，保留同名副本用 `conflict:"keep_both"` |
| `DELETE /api/v1/library/entries` | 永久删除实际文件/文件夹并清理关联索引 | `{"path":"归档/笔记.md"}` |
| `POST /api/v1/library/refresh` | 扫描实际目录并更新变化的处理产物 | `{"path":""}`；可指定单个文件或目录 |
| `POST /api/v1/library/build` | 通过映射构建指定文件或目录 | `{"path":"研究资料","reprocess":false}`；立即返回后台任务，`reprocess:true` 强制重新预处理后构建 |
| `POST /api/v1/import?ingest=false&directory=研究资料` | 保存原文件并转换到独立处理目录 | multipart `file` 字段；目标目录需已存在 |

`GET /library` 的 `data` 包含 `root`（原文件根目录绝对路径）、`directory`（当前相对目录）、`tree`（完整递归树）和 `entries`（当前目录第一层；搜索时为全库名称/路径匹配结果）。单个条目示例：

```json
{"name":"笔记.md","path":"研究资料/笔记.md","kind":"file","size":128,"modified_at":1794499200,"source_id":"source-a","supported":true}
```

目录条目附带 `children`、`size:null`。未建立处理映射的文件 `source_id:null`，也会按实际路径显示。新建文档支持 `.md`、`.markdown` 和 `.txt`；新建目录可逐层嵌套。路径统一使用 `/`，禁止越界、隐藏目录、符号链接和目录联接；移动需保留文件扩展名，且目标父目录必须存在。

改名和移动保留 `source_id`、处理产物路径、内容哈希及 Graph/RAG 索引，更新原文件路径与映射并清理搜索缓存，不调用模型。后台任务排队或执行中拒绝移动。目标已存在时返回冲突，不覆盖文件。

`transfer` 中的 `directory` 是已有目标目录的库内相对路径，空字符串表示根目录。复制保留原始字节，递归复制目录中的实际文件，不复用来源的身份或处理稿；随后刷新或构建时建立独立映射。剪切实际移动原文件并保留原有来源和处理映射。禁止将目录放入自身及其子目录，不跟随符号链接或目录联接。默认同名返回 `409 LIBRARY_ENTRY_EXISTS`；`keep_both` 自动添加“ - 副本”及必要的序号，不覆盖已有项目。没有权限时返回 `403 FILE_ACCESS_DENIED`。

刷新直接发现磁盘中的新增、修改与删除，重新转换变化的受支持原文件；原文件缺失时清理关联来源与索引。返回 `updated`、`deleted` 和 `errors`，逐项报告转换失败。刷新不调用模型，构建通过映射后的 Markdown 路径调用 Graph/RAG。

构建任务在后台队列内完成目录扫描、必要的预处理及 Graph/RAG 构建，可通过 `/api/v1/jobs/{job_id}` 查询进度、成功或失败。普通重建在原文件未变化时保留已编辑的处理稿；`reprocess:true` 则重新转换原文件后构建，两者语义不同。构建期间目录浏览不等待写锁，冲突的文件写操作会明确拒绝。

旧版仅保留处理后 Markdown 的来源不会自动变成原文件。旧索引仍保留；需要原格式预览时重新导入原始资料。旧项目组织端点与 `project` 参数已移除。

Portable Store 使用 `content/originals/` 与 `content/markdown/`，同样按实际目录操作；全部使用 `POST`，并把 `store_root` 和对应参数放在 JSON 请求体中：

```text
POST /api/v1/stores/library/list
POST /api/v1/stores/library/create
POST /api/v1/stores/library/move
POST /api/v1/stores/library/delete
POST /api/v1/stores/library/refresh
```

响应的 `data.store` 携带 Store 身份，`data.result` 是对应操作结果。外部同步记录仍在上游管理，不伪造本地原文件。

---

### 5.2 读取原文件内容与位置

```http
GET /api/v1/documents/{source_id}/content
GET /api/v1/documents/{source_id}/source
GET /api/v1/documents/{source_id}/original
GET /api/v1/library/original?path=研究资料/example.pdf&download=false
GET /api/v1/library/preview?path=研究资料/example.pdf
```

`content` 读取原始文本文件，返回 `source_id`、原文件相对 `relative_path`、`original_path`、原始字节的 `content_hash`、`content`，以及 `processed_content_hash`、`graph_hash`、`rag_hash` 与构建状态。二进制文档使用预览或原文件下载端点。原文件不可用时返回 `404`，请重新导入原始资料。

`source` 返回 `{"path":"研究资料/example.pdf"}`，供检索或图谱来源跳转。`original` 返回原始字节；PDF/常见图片可内联显示，其他格式以附件下载，`download=true` 强制附件。按路径与按 `source_id` 读取遵循同一规则。

### 5.2.1 编辑原始文本

```http
PUT /api/v1/documents/{source_id}/content
Content-Type: application/json
```

```json
{
  "content": "# 修改后的 Markdown\n",
  "expected_content_hash": "读取内容时获得的 64 位 SHA-256"
}
```

`expected_content_hash` 用于防止多个客户端互相覆盖。若文档在读取后已被其他操作更新，返回 HTTP 409 / `CONTENT_CONFLICT`。

保存成功后原子替换原始文本，并重新转换独立处理产物；内容变化时 Graph/RAG 标记为 `pending`。转换失败恢复原文件。旧 `graph_hash`、`rag_hash`、图谱和向量保留至手动构建替换。保存不调用模型。

### 5.2.2 网页预览的知识文档渲染范围

`/library/preview` 读取原文件，返回 `mode`（`markdown`、`text`、`html`、`pdf`、`image`、`download`）、`editable`、名称与大小，以及对应 `content` / `html`。PDF/图片使用原文件 URL；DOCX/DOCM、所有工作表、PPTX/PPTM 全部幻灯片、EPUB 与邮件等直接从原文件生成展示内容，不读取图谱处理产物。Office 展示覆盖文本、图片和表格，不保证桌面 Office 的分页、字体、动画完全一致；二进制格式下载后本地编辑。原文件预览有意独立于索引转换，以保留图片及展示结构，仅共用格式识别。无扩展名文件可识别后预览，但不声明为可编辑文本；HTML/SVG 原文件下载接口始终不允许在应用来源内联执行。

原始 Markdown 的网页渲染支持：

- CommonMark 与 GFM：标题、列表、任务列表、引用、水平分隔线、表格、脚注、删除线、链接、图片和代码块；
- KaTeX 数学公式：`$...$`、`$$...$$`、`\(...\)`、`\[...\]`，以及 `aligned`、`cases`、矩阵、分数、积分、求和、极限和 `\xrightarrow{}` 等常见结构；
- Mermaid 图表 fenced block，按需加载；
- Obsidian 双链 `[[节点]]`、别名双链 `[[节点|显示名]]`、Callout `> [!NOTE]` 和 `![[嵌入]]` 安全占位；
- YAML/TOML frontmatter 识别与代码语言高亮。

网页预览默认不执行原始 HTML、`<script>` 或任意导入文档脚本。KaTeX 或 Mermaid 语法错误只影响当前公式/图表，并保留原始文本，不会让整个文档预览失败。API 调用方若不使用网页端，应将 `content` 交给自己的 Markdown/数学渲染器；不要把 `content` 当作已经清洗过的 HTML 直接插入页面。

### 5.2.3 构建页处理稿与重建

| 方法与端点 | 用途 |
| --- | --- |
| `GET /api/v1/documents/{source_id}/processed` | 读取独立处理目录中的 Markdown，不读取原文件 |
| `PUT /api/v1/documents/{source_id}/processed` | 保存处理稿；必填 `content` 和 `expected_content_hash`（64 位 SHA-256） |
| `POST /api/v1/documents/{source_id}/rebuild` | 后台重新构建此文件的 Graph 与 RAG，包括已经就绪的链路；无需请求体 |
| `POST /api/v1/build/retry-failed` | 后台只重试失败来源的失败链路，不执行其他待处理/就绪链路；无需请求体 |

处理稿读取/保存返回 `source_id`、`source_path`（原文件相对路径，可为空）、`original_path`、`relative_path`（处理稿相对路径）、`processed_path`（处理稿绝对路径）、`size`（UTF-8 字节数）、`modified_at`、`content_hash`、`content`、`graph_status` 和 `rag_status`；保存额外返回 `changed`。

保存仅原子替换处理稿。内容发生变化时两条链路标记为 `pending`，保留旧图谱、向量及构建哈希；不改原文件或目录映射，不调用模型。版本冲突或知识库正被其他线程写入时返回 HTTP 409 / `CONTENT_CONFLICT`，不会覆盖编辑稿。超过 50 MB 返回 HTTP 413。原文件未变化的后续刷新不会覆盖手工修订的处理稿；原文件变化并重新转换后则以新的转换结果为准。

重建端点返回持久化维护任务，使用 `GET /jobs/{job_id}` 跟踪。网页“保存并重新构建”先保存处理稿，再提交单文件重建；如果提交失败，会明确保留“已保存、未构建”的状态，可通过文档卡片再次重建。“确认保存，不构建”只调用处理稿保存端点。定位原文件仍使用 `source_id` 对应的 `/documents/{source_id}/source` 映射。

---

### 5.3 上传并导入文档

```http
POST /api/v1/import?ingest=true
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `file` | binary | 是 | 单个本地文档 |
| `ingest` | query bool | 否，默认 `true` | 是否转换后立即构建图谱和 RAG |
| `directory` | query string | 否，默认根目录 | 已存在的原文件相对目录 |

支持扩展名：

```text
.pdf, .docx, .docm, .pptx, .pptm, .xlsx, .xlsm, .xls, .epub, .rtf, .eml,
.md, .markdown, .txt, .log, .html, .htm, .rst, .csv, .tsv,
.json, .jsonl, .ndjson, .yaml, .yml, .xml
```

最大文件：**50 MB**；转换结果上限为 **20,000,000 字符**，两种超限均返回 `413 FILE_TOO_LARGE`。

无扩展名文件由 Magika 识别，识别结果仍须属于注册表支持的格式；未知二进制不会回退成文本。已有扩展名优先，不自动修复错误扩展名。`detected_format` 返回实际采用的格式。

DOCM 必须安装 Mammoth 与 markdownify，不回退到 python-docx；PPTM 使用 python-pptx，XLSM 使用 openpyxl。只读取文档内容，不执行 VBA。Office 模板、放映、加载项、XLSB 及其他未注册的 `application/vnd.ms-*` 格式不在支持范围内，完整表见 `markitdown/README.md`。

文本类文件会自动识别 UTF-8、UTF-16、GB18030、Big5 等常见编码；CSV 会同时检测编码与分隔符。
PDF 仅提取已有文本层，不执行 OCR。可识别的网格表格输出为 Markdown 表格，保留周边正文；无有效表格的页面沿用原文本输出。扫描版 PDF 会返回 `CONVERSION_FAILED`，应由主智能体先完成 OCR 再导入。

导入接口只接受本地普通文件。URL、网页抓取、视频、音频、远程链接和 OCR 不在 kemo-graph 转换层内处理；这些内容应由上游智能体先归一化为 Markdown 或本地文档。

同一转换逻辑也可在项目根目录直接使用：

```powershell
python -m markitdown C:\path\to\document.pdf -o C:\path\to\document.md
python convert.py C:\path\to\document.docx -o C:\path\to\document.md
```

Python 调用方式：

```python
from markitdown import MarkItDown

result = MarkItDown().convert("document.pdf")
markdown = result.text_content
```

CLI、Python API、网页导入和 `provider.tools.document_tools` 共用同一个 Converter 注册表，不会维护多套格式转换逻辑。

PowerShell 示例：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/import?ingest=false" `
  -Form @{ file = Get-Item $env:KEMO_GRAPH_IMPORT_FILE }
```

成功响应：

```json
{
  "ok": true,
  "data": {
    "source_id": "source-uuid",
    "original_filename": "example.pdf",
    "source_path": "example.pdf",
    "original_path": "E:/kemo-graph/external/originals/example.pdf",
    "detected_format": "pdf",
    "markdown_relative_path": "markdown/example-a1b2c3d4.md",
    "conversion_status": "completed",
    "ingest_status": "pending",
    "size": 123456,
    "origin_hash": "原文件 SHA-256",
    "content_hash": "规范 Markdown SHA-256",
    "origin_modified_at": "UTC ISO 时间"
  },
  "error": null
}
```

`ingest=true` 时，`ingest_status` 可能为：

```text
completed  # 图谱和 RAG 整理成功
failed     # Markdown 已保留，但整理失败；可稍后重试
```

`failed` 的成功导入响应会携带 `ingest_error` 和可选的 `ingest` 摘要；调用方可随后调用 [5.5 整理文档](#55-整理文档)。

导入先保留原文件，再生成独立 Markdown。同目录同名文件仅允许相同字节重复导入；不同内容返回 `409 CONTENT_CONFLICT`。修改已管理文件请使用原文件编辑接口或直接修改磁盘原文件后刷新。转换失败时原文件仍保留，错误会报告给调用方。

---

### 5.4 原始文本上传

```http
POST /api/v1/upload
Content-Type: application/json
```

请求：

```json
{
  "filename": "note.md",
  "content": "# 标题\n\n正文"
}
```

该端点将已有文本按 UTF-8 字节保存在原文件根目录，再转换并注册为待构建文档，不调用模型。文件名不能包含路径分隔符或越界路径。

对于 PDF、Office、EPUB、RTF 等二进制文件，必须使用 `/import`。

---

### 5.5 整理文档

```http
POST /api/v1/ingest
Content-Type: application/json
```

请求：

```json
{
  "paths": ["markdown/example-a1b2c3d4.md"],
  "mode": "both"
}
```

字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `paths` | string[] / null | 否 | 原文件/目录相对路径或处理产物相对路径；原文件先刷新并映射到处理路径，`null` 表示刷新原文件并扫描所有待处理文档 |
| `mode` | string | 否，默认 `both` | `graph`、`rag`、`both` |

返回包括：

```text
processed
graph_updated
rag_updated
failed
details
```

增量规则：内容哈希没有变化的文档不会重复调用 LLM、Embedding 或 Rerank。

---

### 5.6 删除源文档

```http
DELETE /api/v1/documents/{source_id}
```

这是破坏性操作。执行前建议：

1. `GET /documents` 确认 `source_id`；
2. 必要时 `GET /documents/{source_id}/content` 审核内容；
3. 再发起 DELETE。

内部结果：

```text
原文件 → 永久删除
处理 Markdown → 永久删除
sources.exists_status → deleted
删除该 source 的 node_sources / edge_sources / chunks / embeddings / chunk_nodes
同步删除 FAISS 向量
重算仍有来源的节点引用数和边权重
```

文件与派生数据删除完成后，来源记录标记为 deleted；派生数据清理失败时保留活动来源记录，以便重试。删除操作不可恢复。

响应包含：

```text
deleted_source_id
relative_path
graph_deleted
rag_deleted
```

### 5.7 批量删除与当前库全部删除

```http
POST   /api/v1/documents/delete-batch
DELETE /api/v1/documents?confirm=delete-all
DELETE /api/v1/documents?confirm=delete-all&directory=研究资料
```

批量请求体：

```json
{"source_ids": ["source-uuid-1", "source-uuid-2"]}
```

两者只作用于当前 API 实例，不跨 Portable Store。返回 `requested / deleted / failed / documents / failures`，批量操作逐篇报告失败。调用方执行前确认目标和数量，清空端点要求 `confirm=delete-all`。`directory` 非空时清理该目录及子目录的已索引文档，空字符串限定根目录，省略表示全库。实际文件夹及未索引文件的删除使用 `/library/entries`。

---

## 6. 图谱节点与关系 API

### 6.1 获取节点详情

```http
GET /api/v1/nodes/{node_id}
```

返回节点摘要、别名、标签、节点权重、引用次数、全部来源绑定、双向关系和 `groups`（该节点所属的真实节点群总结）。来源包含 `source_path`（库内原文件相对路径，未映射时为空）、`original_path`、`relative_path`（处理稿相对路径）、`origin_hash`、处理状态、证据文本与证据权重。不要将处理稿路径当作原文件路径。

#### 节点全文搜索

```http
GET /api/v1/graph/search?query=知识%20图谱&limit=20&offset=0
```

沿用 kemo-agent 历史与记忆检索的 SQLite FTS5/simple 方案，`jieba_query(query,0)` 中英文分词，查询词之间为 AND，按 BM25 排序。检索节点名称、摘要、别名、标签；名称和别名权重更高。不调用模型或向量服务，不启用拼音扩展。

`query` 最多 1000 字符且不能含 NUL；`limit` 为 1–100，默认 20；`offset` 默认 0。返回 `query`、`engine: "sqlite-fts5/simple"`、`total`、`limit`、`offset`、`nodes`。每个节点包含 `node_id`、`keyword`、`summary`、`aliases`、`tags`、`ref_count`、`rank`（越小越相关）和纯文本 `snippet`。

首次搜索自动为既有节点回填索引；后续节点增删改与索引通过同一 SQLite 事务同步，回滚不留下残留词。索引是派生数据，节点表仍为权威数据。依赖、平台要求与离线重新打包方式见 `core/sqlite_simple/README.md`。

#### 编辑节点摘要

```http
PATCH /api/v1/nodes/{node_id}
Content-Type: application/json

{"summary": "新的节点摘要", "expected_summary": "读取时的节点摘要"}
```

两个字段均必填、各最多 200000 字符。以 `expected_summary` 防止覆盖并发修改，不一致返回 `409 CONTENT_CONFLICT`。仅修改图谱节点，不改原文件、处理稿或来源映射；更新全文索引、清理过期辅助向量与检索缓存，使相关节点群总结失效，待显式“总结节点群”重新生成。返回更新后的节点详情，不自动调用模型。

### 6.2 删除节点

```http
DELETE /api/v1/nodes/{node_id}
```

这是破坏性操作。调用方应先使用 `GET /graph` 或图谱查询确认节点 ID、关系线和来源影响。

删除逻辑：

```text
删除 node_id
  → 删除该节点的全部 edges / edge_sources
  → 删除 node_sources、nodes、chunk_nodes 中相关记录
  → 清空旧 groups / group_nodes，等待后续总结重建
  → 检查每个绑定 source：
      若该 source 没有任何其他节点 → 永久删除原文件、Markdown 及其 Graph/RAG/FAISS 数据
      若仍绑定其他节点 → 只解除当前节点绑定，文件与向量保留
```

响应示例：

```json
{
  "ok": true,
  "data": {
    "deleted_node_id": "node-uuid",
    "deleted_keyword": "知识图谱",
    "cascade_deleted_edges": 3,
    "deleted_files": ["markdown/example.md"],
    "unlinked_files": ["markdown/another.md"]
  },
  "error": null
}
```

### 6.3 获取关系详情

```http
GET /api/v1/relations/{edge_id}
```

返回源/目标节点、关系、权重、支持数和全部文档证据。`path` 固定使用人类可读格式：

```text
A->[关系]->B
```

### 6.4 删除关系

```http
DELETE /api/v1/relations/{edge_id}
```

这是破坏性操作。系统删除关系及全部 `edge_sources` 证据，使旧节点群总结失效，同步清理群组辅助向量与搜索缓存。节点和源文档本身不会因单独删除关系而删除。

---

## 7. 配置与维护 API

这些端点同样在外部 API 中可用，但属于管理操作。自动化智能体不应擅自修改配置或运行维护动作。

### 7.1 获取配置

```http
GET /api/v1/config
```

返回可读取的当前配置。`kemo.api_key` 若已显式配置只返回固定掩码 `************`，并增加只读 `kemo.api_key_source`（`config / environment / none`）；真实密钥不会回显。

### 7.2 保存完整配置

```http
PUT /api/v1/config
Content-Type: application/json
```

请求体保持 JSON 配置对象。服务端校验后原子写入 UTF-8 YAML 文件 `config/config.yaml`，不再读取旧 JSON 配置或 `.env`。`data_dir`、`external_dir`、`web.host` 与 `web.port` 也统一在该文件配置；运行目录或监听地址改变后需重启服务。

**风险**：这是全量配置更新，不是 JSON Patch。调用方必须先 `GET /config`，只修改必要字段后带回完整对象；不要自行编造或删除未知配置项。

密钥优先级：非空 `kemo.api_key` > `kemo.api_key_env` 指定的环境变量。把掩码原样提交会保留现有显式密钥；把字段清空并保存会删除显式密钥并回退到环境变量。`kemo.api_key_source` 和 `kemo.protocol_version` 是 Web 只读字段。

图谱抽取颗粒度由以下两个字段共同控制：

- `graph_extract_granularity`：`small`、`medium` 或 `large`。分别对应细、标准、粗三档；当前默认是 `large`，优先控制节点和关系密度。细粒度会产生更多 Markdown 语义分段，粗粒度请求更少且每段有更严格的实体/关系预算。为兼容旧部署模板，也接受 `fine`、`balanced`、`coarse`，服务端会规范化为上述三档。
- `graph_extract_chunk_size`：颗粒度档位使用的基准字符上限，范围为 `2000~100000`。实际分段上限为基准值乘以档位倍率（`small=0.5`、`medium=1`、`large=2`），并限制在同一范围内。

每段默认预算为：`large` 最多 12 个实体/16 条关系，`medium` 最多 20 个实体/28 条关系，`small` 最多 36 个实体/54 条关系。预算是硬上限而不是填充目标；细节应并入节点 `summary`，不能为了凑数创建碎片节点或共现关系。

修改后，下一次来源扫描会根据图谱抽取配置指纹把已完成文档的 `graph_status` 标为 `pending`；随后可运行网页批量重建或 `python start.py rebuild-knowledge-base`。RAG 配置未变化时不会因此重做向量索引。配置保存动作本身不会直接删除已提交的 Graph/RAG 数据。

### 7.2.1 获取上游可选模型

```http
GET /api/v1/config/models
```

使用服务端已保存的网关地址和密钥读取 Kemo `GET /model/models`，沿用配置文件密钥优先、环境变量次之的认证规则。不会调用模型生成接口，也不会将真实密钥返回浏览器。请求超时最多 20 秒，响应禁止缓存。

成功响应的 `data.models` 为模型数组，每项包含 `id`、`provider_id` 和 `task`（`llm / embedding / rerank / unknown`）。模型范围由上游密钥权限决定；网页按明确的 `task` 分类展示，不通过模型名称猜测用途。

未配置密钥返回 `400 / MODEL_CATALOG_NOT_CONFIGURED`；认证失败返回 `502 / MODEL_CATALOG_AUTH_FAILED`；超时返回 `504 / MODEL_CATALOG_TIMEOUT`；网络、上游接口或目录格式错误返回 `502 / MODEL_CATALOG_UNAVAILABLE`。错误响应不转发上游正文或凭据。

网页在读取或保存配置后自动更新模型目录，支持搜索、刷新和点击选择；选择结果仍需通过保存配置应用。目录为空或拉取失败时保留当前模型 ID，并允许手动填写。

### 7.3 手动生成节点群总结

```http
POST /api/v1/maintenance/summarize
```

只有累计变动文件数量达到 `summary_trigger_file_count` 时才会真正生成总结；否则返回跳过原因。该动作可能调用图谱 LLM。

### 7.6 后台整理文档

```http
POST /api/v1/jobs/ingest
Content-Type: application/json

{"paths": null, "mode": "both", "retry_failed": false}
```

与同步 `/ingest` 使用相同参数，但立即返回 job。网页导入完成后使用此端点，避免页面切换或请求连接中断影响长任务。`retry_failed=true` 时会把此前状态为 `failed` 的 Graph/RAG 文档重新纳入本次扫描；默认只处理 pending 文档。同步 `POST /api/v1/ingest` 也接受同一字段。

### 7.7 后台总结节点群

```http
POST /api/v1/jobs/summarize
```

立即返回 `summarize` job，在后台根据图谱连通分量生成节点群摘要。网页端可通过 `/jobs/{job_id}` 或顶部“运行记录”持续追踪，不受页面切换影响；该任务可能调用图谱 LLM。

### 7.8 知识图谱整理

```http
POST /api/v1/maintenance/organize-graph
Content-Type: application/json

{"use_llm": true, "summarize": true}
```

只整理 Graph 投影：合并已检查的重叠节点/关系、迁移来源事实、重算权重和 `chunk_nodes`。不重读 Markdown，不调用 Embedding。

### 7.9 变化文档知识库重建

```http
POST /api/v1/maintenance/rebuild-knowledge-base
```

重试新增、变化、删除和失败文档；未变化文档按哈希跳过。

### 7.10 全项目影子重建

```http
POST /api/v1/maintenance/rebuild-all
```

在影子目录从全部活动 Markdown 重建 Graph、RAG、FAISS 和派生关联。校验通过才切换正式目录，并在 `data/` 同级保留旧知识库备份；失败不改变正式库。

### 7.11 查询后台任务

```http
GET /api/v1/jobs?limit=100
GET /api/v1/jobs/{job_id}
```

| 字段 | 说明 |
|---|---|
| `job_id` / `kind` | 稳定任务身份与类型 |
| `status` | `queued / running / completed / failed` |
| `progress` | 0~1 |
| `detail` | 当前阶段 |
| `result` / `error` | 完成结果或失败摘要 |
| `created_at` / `started_at` / `finished_at` / `updated_at` | UTC ISO 时间 |
| `events[]` | 有序阶段与错误事件 |

任务状态持久化在 `sources.db`。服务重启后遗留的未完成任务会标记为 failed，不会永久卡住。

### 7.12 检查与安装应用更新

```http
GET  /api/v1/update/status
POST /api/v1/update/check
POST /api/v1/update/apply
```

- `status` 只读取本地版本、上次检查状态和 Git 工作区预检，不访问网络。
- `check` 从公开仓库 `kesepain-KE/kemo-graph` 的 `main/version.yaml` 检查最新 SemVer。
- 根目录 `update.py` 在本地与远端版本相同时也会询问是否强制重新执行更新；
  用户确认后仍必须通过 Git 快进、工作区保护、依赖安装和前端构建检查。
- `apply` 将更新作为 `kind=update` 的持久化后台任务提交，只允许来自 `localhost`、`127.0.0.1` 或 `::1` 的请求。
  旧客户端可以发送空请求体；需要同版本重装时发送 `{"force": true}`，或使用
  `?force=true`。服务端仅在 `force_update_available=true` 且 `can_force_apply=true`
  时接受强制请求；新版本存在时仍走普通升级路径。
- 网页系统维护仅保留“重启服务”，不再提供检查、下载或强制安装更新按钮；更新功能仍可通过终端与 API 使用。
- `python start.py update --force` 与上述 API 使用同一后台任务和
  更新实现，不会绕过工作区保护。
- 自动安装只支持可安全 fast-forward 的 Git 工作区；程序文件有未提交修改时返回 `409 UPDATE_BLOCKED`。
- Git 合并后依赖安装或前端构建失败时，若没有并发的程序文件修改，会自动回滚到更新前提交并恢复用户配置；
  检测到并发代码修改时会停止自动回滚并报告人工处理要求。
- 用户配置和运行数据不参与源码覆盖。安装成功后响应任务结果中的 `restart_required` 为 `true`，调用方应提示用户重启 Web 服务。
- 终端用户也可在项目根目录无参数运行 `python update.py`；更新实现位于 `update/updater.py`，运行状态和备份位于 `update/runtime/`。

### 7.13 Web 进程底层重启

```http
GET  /api/v1/system/runtime
POST /api/v1/system/restart
```

重启请求体必须显式确认：

```json
{"confirm": "restart"}
```

- 只允许本机调用，并且仅在使用 `python start_web.py` 启动时可用。
- 服务先创建独立重启守护器，再向 Uvicorn 请求优雅退出；调度器与维护任务管理器会执行正常关闭流程。
- 存在运行中或排队中的后台任务、每日维护正在执行时，服务会拒绝重启，避免中断数据库和索引写入。
- 守护器等待旧 PID 完全消失和端口释放，再用原始参数启动新的 Python 解释器。
- `runtime` 返回当前 PID，网页据此确认连接到的是新进程，而不是短暂仍存活的旧实例。
- 也可以在项目根目录运行 `python restart.py`，该模块会读取 `update/runtime/web-runtime.json` 并调用同一重启流程。

---

### 7.14 只读运行日志

```http
GET /api/v1/system/logs?category=query&date=2026-09-13&limit=200
```

- `category`：`terminal`（Web 启停及 Python/Uvicorn 日志）、`query`（检索生命周期、缓存和召回事件）、`internal`（导入、建库、维护等其他事件）；默认 `internal`。
- `date`：可选 UTC 日期 `YYYY-MM-DD`，默认当天；不接受文件名或磁盘路径。
- `limit`：1–500，默认 200。仅扫描所选日志末尾 2 MB，结果按旧到新排列。
- `data` 包含 `category`、`date`、`timezone: "UTC"`、`limit`、`available`、`truncated` 和 `entries`。
- 每条记录包含 `id`、`time`、`level`、`module`、`action`、`detail`、`elapsed_ms`；无耗时为 `"-"`。
- `available=false` 表示当日文件尚不存在，返回空数组，不创建日志文件；有文件但没有匹配分类时也返回空数组。
- `truncated=true` 表示扫描窗口或条数受到限制，不代表已读取该日完整日志。

日志来源固定为配置 `log_dir` 下的 `YYYY-MM-DD.tsv`，终端副本位于其 `terminal/` 子目录。接口只读、不提供日志删除或任意文件读取。输出为脱敏纯文本，不应当作 HTML 执行；仍应保护部署服务的访问边界，因为日志可能包含业务文件名和内部运行信息。

未变化的日志通过进程内有界读取缓存复用，文件追加、替换或脱敏密钥变化后重新读取。文档列表、知识库状态和搜索所用知识库指纹也使用同一内存读取层；缓存最多 256 项／32 MiB 序列化载荷，单项最多 2 MiB、有效期最多 30 秒，按绝对 Store 路径隔离。每次读取检查对应文件变化，数据库额外检查文件头；存在非空 WAL／事务日志时绕过数据库缓存。HTTP 参数与响应格式不变，不新增延迟写入机制。

新增终端记录从服务重启后开始；它不是交互式终端，也不能读取过去未保存的输出。查询日志对网页、API、CLI 使用的公共检索链路生效；`query_start` / `query_complete` / `query_failed` 通过 `query_id` 关联，不额外记录问题正文、回答或认证请求头。

### 7.15 检索步骤进度（可选）

标准 `POST /query/graph`、`/query/rag`、`/query/hybrid`、`/query/answer`、`/query/global` 可携带 `X-Kemo-Progress-Id` 请求头，值为客户端生成的随机 UUID。原查询请求体、响应包络和结果结构不变；不携带该请求头时，仍按原方式执行。

```http
GET /api/v1/query/progress/{progress_id}
```

返回 `data.available`、`data.status` 与 `data.steps`。步骤包含 `id`、简短 `label` 和 `status`（`running` / `completed` / `failed` / `fallback`）；仅记录实际执行环节，不提供估算百分比。不存在、已过期或请求落在其他工作进程时返回 `available=false, steps=[]`。

记录只驻留当前应用进程内存，最多 128 项、最长保留 10 分钟，不存查询正文、模型回答或认证信息；容量不足时放弃新进度记录，不阻止检索。进度 UUID 应每次查询重新随机生成并按临时访问凭据保护，勿复用；重复 ID 不覆盖既有记录。网页每秒轮询，检索结束后停止；轮询失败不影响实际查询。当前独立 Store 查询接口未接入此步骤通道。多工作进程部署需使用同进程路由／粘性会话；重启后进度清空。

## 8. 智能体推荐调用流程

### 8.1 只导入、不消耗模型额度

```text
POST /import?ingest=false
  → GET /documents
  → GET /documents/{source_id}/content
  → 审核转换后的 Markdown
```

之后再决定是否：

```text
POST /jobs/ingest {"paths":["…"], "mode":"both"}
  → GET /jobs/{job_id}
```

### 8.2 普通查询

```text
GET /status
  → POST /query/hybrid
```

若只需要概念关系，使用 `POST /query/graph`；若只需要原文片段，使用 `POST /query/rag`。

### 8.3 删除前的安全流程

```text
删除文档：
GET /documents → GET /documents/{source_id}/content → DELETE /documents/{source_id}

删除节点：
GET /graph 或 POST /query/graph → 检查节点/边/来源影响 → DELETE /nodes/{node_id}
```

不要依据 keyword 猜测 UUID；始终读取实际 `source_id`、`node_id`。

---

## 9. 已知能力边界

### 9.1 不支持远程 URL 导入

`/import` 只接收上传的本地文件，不接收 URL。调用方若持有网络 URL，应先自行安全下载、校验后再上传。

### 9.2 文件删除不可恢复

删除原文件或节点关联的独占文档时，直接永久删除相应文件及索引。网页执行前要求确认。

---

## 10. 运行与日志

运行日志默认按 UTC 日期写入：

```text
<configured-log-dir>/YYYY-MM-DD.tsv
```

文件日志、终端日志与重启辅助日志统一采用 TSV 表格；网页以相同字段展示：

| 列 | 含义 |
|---|---|
| `time` | UTC 时间 |
| `level` | DEBUG / INFO / WARNING / ERROR |
| `module` | 产生日志的模块 |
| `action` | 运行事件或动作 |
| `detail` | 脱敏后的单行详情 |
| `elapsed_ms` | 毫秒耗时；无耗时为 `-` |

`config/logging.yaml` 控制标准 Python logging 的 TSV 终端输出；重启辅助日志位于 `update/runtime/restart.tsv`。HTTP 日志接口仍返回带命名字段的 JSON 行记录，便于客户端生成表格。

日志会记录导入、图谱构建、RAG、FAISS、删除、维护和 API 错误的摘要；不会记录 API Key、Bearer Token、完整文档正文或完整模型提示词。

对外 API 实现自身可独立启动：

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

如需在自动化环境运行，调用方应显式配置并保护：

```text
KEMO_API_KEY
KEMO_GRAPH_CONFIG
KEMO_GRAPH_WEB_HOST
KEMO_GRAPH_WEB_PORT
```

---

## 11. 绝对路径分布式 Store API

面向 kemo-agent 和组织级全局扩展，服务可在任意绝对知识位置建立独立数据库与索引。调用方传入知识位置 `store_root`，系统固定使用：

```text
<store_root>\kemo-graph-storage\
```

本文不绑定源码版安装目录、Windows 盘符或部署版容器路径。所有
`<configured-...-root>`、`<absolute-source-file-from-host-config>` 都是部署配置
占位符，不能作为字面值发送。全局拓展必须从自身配置读取路径，在运行时解析为当前
操作系统的绝对路径，再放入 JSON 请求体；不得根据 kemo-graph 的安装目录猜测数据
位置。

其中包含该位置独立的 `sources.db`、`search_cache.db`、`Graph/graph.db`、`RAG/rag.db`、FAISS 索引与规范 Markdown。不同 Store 不共享数据库；联合查询只在内存中融合。

### 11.1 初始化

```http
POST /api/v1/stores/initialize
Content-Type: application/json
```

```json
{
  "store_root": "<configured-user-memory-root>",
  "scope": "memory.user",
  "owner_id": "alice",
  "display_name": "Alice 的统一记忆索引"
}
```

`scope` 取值：

```text
knowledge.global
knowledge.shared
knowledge.user
memory.temporary
memory.important
memory.permanent
memory.user
```

`memory.user` 面向 kemo-agent 当前的每用户记忆表存储：同一用户的四档记忆共享
一个 Store，不能再按 tier 拆成多个数据库。旧三档 memory scope 仅为已有调用方
保留兼容。

重复初始化是幂等的，并保持原 `store_id`。已有 Store 不允许静默变更 scope/owner。

### 11.2 响应身份

除 initialize/info 与 federated 外，单 Store 响应的 `data` 统一为：

```json
{
  "store": {
    "store_id": "uuid",
    "scope": "memory.user",
    "owner_id": "alice",
    "root_path": "<configured-user-memory-root>"
  },
  "result": {}
}
```

调用方应使用 `store_id` 与 `scope` 归属结果，不能只依赖路径字符串。

### 11.3 完整端点索引

```text
# 生命周期和导入
POST /api/v1/stores/initialize
POST /api/v1/stores/info
POST /api/v1/stores/status
POST /api/v1/stores/import-path
POST /api/v1/stores/import
POST /api/v1/stores/upload
POST /api/v1/stores/ingest
POST /api/v1/stores/sources/sync
POST /api/v1/stores/sources/status
POST /api/v1/stores/sources/delete

# 检索与回答
POST /api/v1/stores/query/graph
POST /api/v1/stores/query/rag
POST /api/v1/stores/query/hybrid
POST /api/v1/stores/query/answer
POST /api/v1/stores/query/global
POST /api/v1/stores/query/federated

# 文档、节点、关系
POST /api/v1/stores/documents/list
POST /api/v1/stores/library/list
POST /api/v1/stores/library/create
POST /api/v1/stores/library/move
POST /api/v1/stores/library/delete
POST /api/v1/stores/library/refresh
POST /api/v1/stores/documents/content
POST /api/v1/stores/documents/update
POST /api/v1/stores/documents/delete
POST /api/v1/stores/documents/delete-batch
POST /api/v1/stores/documents/delete-all
POST /api/v1/stores/nodes/get
POST /api/v1/stores/nodes/delete
POST /api/v1/stores/relations/get
POST /api/v1/stores/relations/delete

# 图谱和 GPU 分页
POST /api/v1/stores/graph/full
POST /api/v1/stores/graph/visualization/meta
POST /api/v1/stores/graph/visualization/nodes
POST /api/v1/stores/graph/visualization/edges
POST /api/v1/stores/graph/neighborhood

# 搜索缓存
POST /api/v1/stores/cache/list
POST /api/v1/stores/cache/show
POST /api/v1/stores/cache/clear

# 维护与任务历史
POST /api/v1/stores/maintenance/organize-graph
POST /api/v1/stores/maintenance/rebuild-knowledge-base
POST /api/v1/stores/maintenance/rebuild-all
POST /api/v1/stores/maintenance/summarize
POST /api/v1/stores/jobs/list
POST /api/v1/stores/jobs/get
```

绝对路径放在 JSON body，不放入 URL path/query。Store 维护端点当前同步执行，以免任务被错误提交到默认知识库的后台管理器。

### 11.4 外部表记录同步

kemo-agent 的记忆以 SQLite 表为权威来源。kemo-graph 不直接写该数据库，而由
全局扩展把记录转换为稳定来源包络并推送：

```http
POST /api/v1/stores/sources/sync
Content-Type: application/json
```

```json
{
  "store_root": "<configured-user-memory-root>",
  "ingest_after_sync": false,
  "records": [
    {
      "source_uri": "kemo-agent://users/alice/memory/preferences.md",
      "source_type": "memory.fragment",
      "display_name": "preferences.md",
      "content": "# 用户偏好\n\n回答保持简洁。",
      "revision": "3",
      "updated_at": "2026-08-05T12:00:00+00:00",
      "metadata": {"tier": "permanent", "weight": 2},
      "deleted": false,
      "ingest_mode": "both"
    }
  ]
}
```

同步规则：

- `source_uri` 是跨重启稳定身份；记忆 tier 晋升不得改变 URI；
- `revision` 相同但规范正文不同记为 `conflict`，不会覆盖现有数据；
- `updated_at` 早于现有记录记为 `stale`；
- 正文相同但 revision/metadata 改变时只更新元数据，不重建 Graph/RAG；
- 正文变化后 Graph/RAG 变为 pending；`ingest_after_sync=true` 可立即整理；
- `deleted=true` 是 tombstone：立即级联清理 Graph/RAG 和派生 Markdown，直接删除；
- `content_hash` 是上游正文哈希，`sources.content_hash` 是规范 Markdown 哈希，两者分别保存；
- 批次上限 1000 条，单条正文 50 MiB，metadata 64 KiB。

查看状态：

```http
POST /api/v1/stores/sources/status
```

```json
{
  "store_root": "<configured-user-memory-root>",
  "source_type": "memory.fragment",
  "include_deleted": false,
  "page": 1,
  "page_size": 100
}
```

显式删除多个稳定来源：

```http
POST /api/v1/stores/sources/delete
```

```json
{
  "store_root": "<configured-user-memory-root>",
  "source_uris": ["kemo-agent://users/alice/memory/preferences.md"]
}
```

### 11.5 绝对路径导入

```http
POST /api/v1/stores/import-path
```

```json
{
  "store_root": "<configured-user-knowledge-root>",
  "path": "<absolute-source-file-from-host-config>",
  "ingest_after_import": false,
  "expected_origin_hash": "<optional-64-char-source-sha256>"
}
```

`path` 必须为绝对普通文件路径。`expected_origin_hash` 可省略；可靠调用方应传入自己刚扫描得到的原文件 SHA-256。服务端会先把源文件捕获为私有临时快照，确认快照哈希匹配后再转换；哈希不一致或捕获期间文件变化时返回 HTTP 409 / `IMPORT_SOURCE_CHANGED`，不会注册该文档。返回同时包含原文件 `origin_hash` 与规范 Markdown `content_hash`，二者不得混用。

### 11.6 multipart 文件上传导入

调用方与 kemo-graph 不在同一文件系统，或不希望授予服务端源文件读取权限时，使用：

```http
POST /api/v1/stores/import?ingest=false
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `store_root` | text | 是 | 已初始化 portable Store 的绝对路径；放在 multipart 表单中，不进入 URL 日志 |
| `file` | binary | 是 | 单个待转换文档，最大 50 MB |
| `ingest` | query bool | 否，默认 `false` | 转换导入后是否立即构建 Graph 与 RAG |

该端点与 `/import` 共用文件名、扩展名、大小、转换和 ingest 失败校验。响应的 `data`
同时包含稳定 `store` 身份和 `result` 导入结果。portable 上传默认只转换并登记为待整理，
需要立即整理时必须显式传入 `ingest=true`。

### 11.7 联合查询

```http
POST /api/v1/stores/query/federated
```

```json
{
  "store_roots": [
    "<configured-global-knowledge-root>",
    "<configured-user-memory-root>"
  ],
  "query": "问题",
  "mode": "hybrid",
  "top_k": 20,
  "graph_depth": 3,
  "force": false
}
```

支持 `graph/rag/hybrid/global/answer`。响应中的每条 `merged_results` 带 Store 身份和 `federated_score`。单 Store 失败记录在 `stores_failed`，不会取消其他 Store 的成功结果；调用方必须检查成功数与失败列表。

### 11.8 给 kemo-agent 全局拓展的推荐数据域映射

全局拓展应把一个 `store_root` 理解为一个**数据域位置**，而不是一条记录或一个
记忆 tier。每个数据域只创建一个可见的 `kemo-graph-storage`：

| 上游数据域 | 推荐 `store_root` | scope | 写入方式 |
|---|---|---|---|
| 全局知识文件 | `<agent_root>/global_knowledge` | `knowledge.global` | `stores/import-path` |
| 跨用户共享知识文件 | `<agent_root>/shared_knowledge` | `knowledge.shared` | `stores/import-path` |
| 用户知识文件 | `<agent_root>/users/<user>/knowledge` | `knowledge.user` | `stores/import-path` |
| 用户记忆表 | `<agent_root>/users/<user>/improve` | `memory.user` | `stores/sources/sync` |

边界规则：

- 知识库仍以文件为权威来源；全局拓展枚举允许格式的文件并逐个调用
  `import-path`，不要把文件正文伪装成记忆表记录；
- `memory.sqlite3` 是 kemo-agent 的权威业务库；kemo-graph 不直接写入，也不要求
  获得 SQLite 写权限；
- 同一用户的 `seven_days / one_month / half_year / permanent` 记忆 tier
  全部进入同一个 `memory.user` Store，tier 放在 `metadata.tier`；
- 不要为单条记忆、单个会话或单个文件创建独立 Store；这会产生过多 SQLite、
  FAISS 和搜索缓存文件，也会让联合检索成本线性放大。

### 11.9 全局拓展启动与刷新顺序

推荐的启动握手：

```text
1. POST /stores/initialize  幂等确保每个已配置数据域存在
2. POST /stores/info        保存并核对 store_id、scope、owner_id、root_path
3. POST /stores/status      读取文档、Graph、RAG、FAISS 健康状态
4. 推送本轮变化文件/记录
5. 对发生正文变化的 Store 执行 /stores/ingest
6. 再次调用 /stores/status；必要时 POST /stores/documents/list 并在 body 传 status=pending
7. 对用户查询调用单 Store hybrid，或调用 federated 跨域融合
```

`stores/initialize` 可以安全重复调用；scope 或 owner 与现有 manifest 不一致时不会
静默改写，而是返回 `STORE_INVALID`。全局拓展应持久保存响应中的 `store_id`，启动
时同时核对物理路径与稳定 ID，防止路径配置误指向另一套知识库。

初始化成功响应示例（部分字段）：

```json
{
  "ok": true,
  "data": {
    "manifest": {
      "schema_version": 1,
      "store_id": "66c96ea6-d0b3-4e50-accc-d9bc1bc51519",
      "display_name": "Alice 的统一记忆索引",
      "scope": "memory.user",
      "owner_id": "alice",
      "root_path": "<configured-user-memory-root>",
      "storage_directory": "kemo-graph-storage"
    },
    "storage_root": "<configured-user-memory-root>/kemo-graph-storage",
    "initialized_now": true,
    "status": {}
  },
  "error": null
}
```

### 11.10 SourceEnvelope 严格字段契约

`records` 每项字段如下。请求模型拒绝任何未知字段。

| 字段 | 必填 | 限制 | 语义 |
|---|---:|---|---|
| `source_uri` | 是 | 1–2048 字符；小写 URI scheme；批次内唯一 | 跨重启、改名策略和 tier 晋升保持稳定的权威身份 |
| `source_type` | 是 | 1–128；`^[a-z][a-z0-9_.-]*$` | 如 `memory.fragment` |
| `display_name` | 是 | 1–255；只能是文件名，不能含路径或 `..` | 用户可读逻辑名称；不作为身份 |
| `content` | 非删除时是 | UTF-8 规范正文；最大 50 MiB | 用于生成派生 Markdown、Graph 与 RAG |
| `content_hash` | 否 | 64 位 SHA-256 十六进制 | 上游正文哈希；调用方负责计算正确性 |
| `revision` | 是 | 1–255 字符 | 上游不透明版本；可以是整数转字符串、UUID 或业务版本 |
| `updated_at` | 是 | 带时区 ISO 8601；最大 64 字符 | 因果顺序；服务端统一比较 UTC 时间 |
| `metadata` | 否 | JSON 对象；序列化后最大 64 KiB | tier、weight、filename_key 等非正文属性 |
| `deleted` | 否 | 默认 `false` | `true` 表示上游 tombstone；此时 `content` 可省略 |
| `ingest_mode` | 否 | `graph / rag / both`，默认 `both` | 本条正文后续需要构建的投影 |

正文会统一换行为 LF，并保证末尾换行。`content_hash` 是上游提供的身份校验信息；
kemo-graph 另行计算规范 Markdown 的 `sources.content_hash`。两个哈希不能互换。

推荐稳定 URI：

```text
# 记忆：使用不会因 filename/tier 改变的数据库记录 ID
kemo-agent://users/alice/memory/fragments/1842
```

不要把正文哈希放进 `source_uri`；否则正文每次变化都会被识别为新来源，旧 Graph、
RAG 与向量将无法按同一身份增量替换。

#### 11.9.1 memory_fragments 映射建议

| kemo-agent 字段 | SourceEnvelope |
|---|---|
| `id` | `source_uri` 末段稳定 ID |
| `filename` | `display_name` |
| `content` | `content` |
| `content_hash` | `content_hash` |
| `revision` | `revision`（转字符串） |
| `content_updated_at` | `updated_at` |
| `tier / weight / filename_key` | `metadata` |

tier 晋升只产生 metadata/revision 更新，不改变 URI；正文没有变化时服务端会返回
`metadata_updated`，不会重新构建 Graph/RAG。

### 11.11 同步结果、幂等和删除状态机

成功同步返回 HTTP 200。即使某条记录为 `stale` 或 `conflict`，HTTP 仍为 200，
调用方必须检查 `data.result` 中的计数与 `details`：

```json
{
  "ok": true,
  "data": {
    "store": {
      "store_id": "66c96ea6-d0b3-4e50-accc-d9bc1bc51519",
      "scope": "memory.user",
      "owner_id": "alice",
      "root_path": "<configured-user-memory-root>"
    },
    "result": {
      "received": 3,
      "created": 1,
      "updated": 0,
      "metadata_updated": 1,
      "unchanged": 0,
      "deleted": 0,
      "stale": 1,
      "conflicts": 0,
      "failed": 0,
      "details": [
        {
          "source_uri": "kemo-agent://users/alice/memory/fragments/1842",
          "source_id": "kemo-graph-source-uuid",
          "status": "created"
        }
      ],
      "ingest": null,
      "search_cache_deleted": 4
    }
  },
  "error": null
}
```

| 状态 | 条件 | 调用方动作 |
|---|---|---|
| `created` | URI 首次出现 | 等待/触发 ingest |
| `updated` | 正文变化，或删除后恢复 | 等待/触发 ingest |
| `metadata_updated` | 正文相同，仅版本或 metadata 变化 | 无需 ingest |
| `unchanged` | 完全幂等重放，或删除不存在的 URI | 无操作 |
| `deleted` | 活动来源收到 tombstone | 从本地同步游标确认删除已消费 |
| `stale` | `updated_at` 早于现有记录 | 不要重试旧事件；刷新上游游标 |
| `conflict` | 相同 revision 对应不同规范正文 | 停止自动覆盖并记录告警，检查上游版本生成逻辑 |

批次会在写入前统一校验：最多 1000 条，批次内 URI 不得重复。格式校验失败会让
整个请求返回 422，而不会只跳过某一条。

删除有两种入口：

1. **上游同步删除（推荐）**：在 `sources/sync` 中发送 `deleted=true`，同时携带
   上游 revision 与 updated_at，保留完整因果顺序；
2. **运维强制删除**：调用 `sources/delete` 只传 `source_uris`，由服务端生成删除
   revision 与时间，适用于人工清理，不适合作为上游 change feed 的常规路径。

tombstone 会立即删除派生 Markdown，并级联清理 Graph、RAG、FAISS 和搜索缓存；
派生 Markdown 直接删除，上游 SQLite 仍是可恢复的权威来源。以后使用
同一 URI 和更新版本恢复时，会复用原来的 kemo-graph `source_id`。

### 11.12 整理策略与超时

`ingest_after_sync` 的选择：

- `false`（推荐给事件接收线程）：只同步并标记 pending，响应较快；全局拓展的
  后台 worker 再合并变化并调用 `/stores/ingest`；
- `true`：在同一个 HTTP 请求内按每条 `ingest_mode` 直接构建，可能持续数分钟，
  调用方必须配置长超时，且不应占用用户请求线程。

`stores/ingest` 请求：

```json
{
  "store_root": "<configured-user-memory-root>",
  "paths": null,
  "mode": "both"
}
```

`paths=null` 处理当前 Store 的全部 pending 项；指定路径时必须使用 kemo-graph
返回的 `relative_path`，不能传 `source_uri` 或上游绝对路径。调用方可通过
`sources/status` 取得路径，并按业务所需的 `ingest_mode` 分组整理。

整理响应示例：

```json
{
  "processed": 3,
  "graph_updated": 2,
  "rag_updated": 3,
  "skipped": 0,
  "failed": 1,
  "details": [
    {
      "path": "env-reference-eacda38418.md",
      "graph": "failed",
      "rag": "updated",
      "graph_error": "Provider request failed"
    }
  ]
}
```

HTTP 200 不代表每篇整理都成功，必须检查 `failed` 与 `details`。失败项可以通过
指定其 `relative_path` 再次调用 ingest；已经 ready 的另一投影不会重复构建。

建议客户端超时：

| 操作 | 建议总超时 |
|---|---:|
| info/status/source-status | 15 秒 |
| graph/rag/hybrid 查询 | 120 秒 |
| answer/global（包含 LLM） | 300 秒 |
| import-path | 120 秒 |
| ingest/organize/rebuild/summarize | 30 分钟或交给后台 worker |

同一个 Store 的写操作应由全局拓展串行化。不要启动多个 kemo-graph Web 进程同时
写同一套 SQLite/FAISS；构建期间查询可能返回 `409 PROCESSING`。

### 11.13 检索模式选择

| 需求 | 端点 | 是否生成自然语言回答 | 建议 |
|---|---|---:|---|
| 精确概念、关系和路径 | `query/graph` | 否 | 返回节点、边、`A->[关系]->B` 路径 |
| 原文证据与语义相似内容 | `query/rag` | 否 | 返回 chunk、父级上下文、分数和来源 |
| 智能体自行组织最终回答 | `query/hybrid` | 否 | **全局拓展默认选择**；同时返回 Graph 与 RAG |
| 让 kemo-graph 直接回答 | `query/answer` | 是 | 会额外调用 LLM；适合独立知识问答入口 |
| 节点群级全局问题 | `query/global` | 是 | 必须先执行 summarize 生成节点群总结 |
| 跨知识/记忆/历史数据域 | `query/federated` | 取决于 mode | 对各 Store 独立查询后在内存中融合 |

推荐的单 Store 混合检索请求：

```json
{
  "store_root": "<configured-user-memory-root>",
  "query": "用户偏好的回答风格是什么？",
  "graph_depth": 2,
  "rag_top_k": 8,
  "graph_confidence": null,
  "rag_threshold": null,
  "direction": "both",
  "force": false
}
```

- `graph_depth`：1–10；普通问答建议 1–2，系统关系分析再使用 3 以上；
- `rag_top_k`：1–100；`null` 使用服务端配置；
- confidence/threshold 为 `null` 时使用服务端配置；
- `direction`：`forward / backward / both`；
- `force=true` 只绕过搜索结果缓存，不会绕过 processing 状态或数据一致性检查。

混合响应的 `data.result` 主要字段：

```text
graph.hit_nodes       直接命中的节点
graph.expanded_nodes  BFS 扩展节点
graph.edges           机器可读关系边
graph.relations       A->[关系]->B
graph.paths           A->[关系]->B->[关系]->C
graph.groups          相关节点群
rag.results           召回 chunk、父级 context、score、source
entities              实体向量召回
communities           群组总结向量召回
```

全局拓展通常应把这些证据交给 kemo-agent 的主回答模型，而不是再调用
`query/answer`，避免两次 LLM 总结和上下文损失。

### 11.14 跨 Store 联合检索响应

`query/federated` 最多接受 100 个绝对 Store 路径。每个 Store 独立失败隔离：

```json
{
  "ok": true,
  "data": {
    "query": "用户最近关心什么？",
    "mode": "hybrid",
    "stores_requested": 3,
    "stores_succeeded": 2,
    "stores_failed": [
      {
        "store_root": "<configured-user-history-root>",
        "error_type": "PortableStoreNotInitializedError",
        "message": "知识位置尚未初始化"
      }
    ],
    "stores": [
      {
        "store": {
          "store_id": "uuid",
          "scope": "memory.user",
          "owner_id": "alice",
          "store_root": "<configured-user-memory-root>"
        },
        "result": {}
      }
    ],
    "merged_results": []
  },
  "error": null
}
```

必须同时检查 `stores_succeeded` 和 `stores_failed`；HTTP 200 只代表联合查询调度
成功，不代表每个 Store 都可用。`merged_results` 主要融合可排序的 RAG/实体/群组
结果；完整的单 Store Graph 结构仍位于 `stores[i].result`。

### 11.15 状态、分页与变更游标

Store 健康检查：

```http
POST /api/v1/stores/status
```

`data.result`：

```json
{
  "initialized": true,
  "sources": {
    "total": 3,
    "active": 3,
    "pending_graph": 0,
    "pending_rag": 0
  },
  "graph": {
    "total_nodes": 98,
    "total_edges": 135,
    "total_groups": 0
  },
  "rag": {
    "total_chunks": 21,
    "total_vectors": 21,
    "faiss_healthy": true
  }
}
```

`status` 不列出每条失败原因。发现 pending、构建失败或数量异常时，调用：

```text
POST /stores/documents/list  查看 graph_status/rag_status/content_hash
POST /stores/sources/status  查看 URI/revision/metadata/relative_path
POST /stores/jobs/list       查看维护任务记录
POST /stores/jobs/get        查看单任务事件
```

`sources/status` 返回的每项包括：`source_id/source_uri/source_type/revision/`
`source_updated_at/metadata/external_content_hash/last_synced_at/relative_path/`
`content_hash/graph_status/rag_status/exists_status/created_at/updated_at`。

kemo-graph 当前不提供 kemo-agent 上游表的 change feed；全局拓展应在自己的业务层
维护游标，例如 `(content_updated_at, id)` 或业务 revision，并只把变化项推送给
`sources/sync`。接口本身允许安全重放，不能用“可能重复”作为跳过幂等设计的理由。

### 11.16 错误处理与重试矩阵

| HTTP | code | 是否重试 | 全局拓展处理 |
|---:|---|---:|---|
| 403 | `STORE_ACCESS_DENIED` | 否 | 检查绝对路径、`..`、allowed_roots 和进程权限 |
| 404 | `STORE_NOT_INITIALIZED` | 条件式 | 对已授权数据域执行一次 initialize，再重试原请求 |
| 404 | `NOT_FOUND` | 否 | 刷新 source/node/edge ID；不要无限重试旧 ID |
| 409 | `PROCESSING` | 是 | 1、2、4、8 秒退避并设置总等待上限 |
| 409 | `GRAPH_CHANGED` | 是 | 刷新 visualization revision 后重取分页 |
| 409 | `CONTENT_CONFLICT` | 否 | 重新读取最新 content_hash，人工/业务层解决并发编辑 |
| 413 | `FILE_TOO_LARGE` | 否 | 在上游拆分或拒绝文件 |
| 415 | `UNSUPPORTED_FORMAT` | 否 | 上游转换为受支持格式 |
| 422 | `INVALID_PARAM` | 否 | 修正字段、哈希、时间、URI 或分页参数 |
| 422 | `STORE_INVALID` | 否 | 检查 manifest、scope、owner 和目录完整性 |
| 422 | `CONVERSION_FAILED` | 条件式 | 修复文档/编码后重试；扫描 PDF 需先 OCR |
| 502 | `INGEST_FAILED` | 是 | Provider 临时失败可有限重试；保留失败详情 |
| 500 | `INTERNAL` | 有限 | 记录 request 上下文，指数退避最多 2–3 次 |

只有网络错误、超时、`PROCESSING`、临时 Provider/500 错误适合自动重试。422、403、
内容冲突和同步 `conflict` 必须先修正数据或配置。

### 11.17 可直接用于全局拓展的 Python 客户端示例

以下示例只使用 Python 标准库，展示初始化、同步、整理和混合查询。生产实现应把
HTTP 调用放入 kemo-agent 的全局拓展服务层，并增加日志、取消信号和持久化游标。

```python
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get(
    "KEMO_GRAPH_BASE_URL",
    "http://127.0.0.1:8000/api/v1",
).rstrip("/")
STORE_ROOT = os.environ["KEMO_GRAPH_STORE_ROOT"]


class KemoGraphAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code


def post(path: str, payload: dict[str, Any], *, timeout: float = 120) -> Any:
    request = Request(
        BASE_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        error = body.get("error") or {}
        raise KemoGraphAPIError(
            exc.code,
            str(error.get("code") or "HTTP_ERROR"),
            str(error.get("message") or exc.reason),
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"kemo-graph 不可达：{exc.reason}") from exc
    if not body.get("ok"):
        error = body.get("error") or {}
        raise KemoGraphAPIError(
            200,
            str(error.get("code") or "UNKNOWN"),
            str(error.get("message") or "未知错误"),
        )
    return body["data"]


# 1. 幂等初始化
store = post(
    "/stores/initialize",
    {
        "store_root": STORE_ROOT,
        "scope": "memory.user",
        "owner_id": "alice",
        "display_name": "Alice 的统一记忆索引",
    },
)
print("store_id:", store["manifest"]["store_id"])

# 2. 推送一条变化记录；实际连接器应由 memory_store 业务层提供这些字段
sync = post(
    "/stores/sources/sync",
    {
        "store_root": STORE_ROOT,
        "ingest_after_sync": False,
        "records": [
            {
                "source_uri": "kemo-agent://users/alice/memory/fragments/1842",
                "source_type": "memory.fragment",
                "display_name": "preferences.md",
                "content": "# 用户偏好\n\n回答保持简洁。\n",
                "revision": "3",
                "updated_at": "2026-08-05T12:00:00+00:00",
                "metadata": {
                    "tier": "permanent",
                    "weight": 2,
                    "filename_key": "preferences.md",
                },
                "deleted": False,
                "ingest_mode": "both",
            }
        ],
    },
)
summary = sync["result"]
if summary["conflicts"] or summary["failed"]:
    raise RuntimeError(f"来源同步未完成：{summary['details']}")

# 3. 在后台 worker 中整理 pending 内容；模型调用可能耗时较长
if summary["created"] or summary["updated"]:
    ingest = post(
        "/stores/ingest",
        {"store_root": STORE_ROOT, "paths": None, "mode": "both"},
        timeout=1800,
    )
    if ingest["result"]["failed"]:
        raise RuntimeError(f"知识整理存在失败项：{ingest['result']['details']}")

# 4. 让主智能体取得 Graph + RAG 证据，自行组织最终回答
retrieval = post(
    "/stores/query/hybrid",
    {
        "store_root": STORE_ROOT,
        "query": "用户偏好的回答风格是什么？",
        "graph_depth": 2,
        "rag_top_k": 8,
        "graph_confidence": None,
        "rag_threshold": None,
        "direction": "both",
        "force": False,
    },
)
evidence = retrieval["result"]
print(json.dumps(evidence, ensure_ascii=False, indent=2))
```

### 11.18 PowerShell 联调示例

```powershell
$base = if ($env:KEMO_GRAPH_BASE_URL) {
  $env:KEMO_GRAPH_BASE_URL.TrimEnd("/")
} else {
  "http://127.0.0.1:8000/api/v1"
}
$store = $env:KEMO_GRAPH_STORE_ROOT
if (-not $store) {
  throw "请先由部署配置设置 KEMO_GRAPH_STORE_ROOT"
}

$status = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/stores/status" `
  -ContentType "application/json" `
  -Body (@{ store_root = $store } | ConvertTo-Json)

$query = @{
  store_root = $store
  query = "定时任务为什么必须经过 time_plan？"
  graph_depth = 2
  rag_top_k = 8
  graph_confidence = $null
  rag_threshold = $null
  direction = "both"
  force = $false
} | ConvertTo-Json

$result = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/stores/query/hybrid" `
  -ContentType "application/json" `
  -Body $query

$result.data.result | ConvertTo-Json -Depth 20
```

### 11.19 部署与安全底线

- API 当前没有应用层认证；默认仅监听 `127.0.0.1`；
- 全局拓展不应把来自用户输入的任意字符串直接作为 `store_root`，必须从受控的
  用户/数据域配置映射生成；
- 建议设置 `portable_stores.allowed_roots`，限制只能访问 kemo-agent 数据根目录；
- `store_root` 必须是绝对路径，不能含 `..`，也不能直接指向
  `kemo-graph-storage`；
- 不要把 API 暴露到公网。跨主机部署必须在外层提供 TLS、认证、授权和审计；
- 正文会通过 Kemo 网关执行图谱抽取、Embedding、Rerank 或回答，敏感数据策略应
  同时覆盖 kemo-agent、kemo-graph 和网关链路；
- 可通过 `http://127.0.0.1:8000/docs` 查看交互式 OpenAPI，或读取
  `/openapi.json` 生成客户端；实现连接器时仍以本文的生命周期和幂等规则为准。

更完整的目录、清单、迁移和隔离规则见：

> `开发文档/编程方案/16-绝对路径分布式知识库与全量API.md`

### 服务实现入口

Python 调用统一从 `core` 或 `core.service` 导入 `KnowledgeBaseService`。HTTP 路由按领域拆分到 `api/routes/`，请求与响应模型拆分到 `api/schemas/`；路由聚合入口仍为 `api.routes.router`。`api.create_app()` 显式创建应用，`api:app` 通过惰性属性提供，单独 `import api` 不创建应用。路由路径、方法、注册顺序和 OpenAPI 定义保持不变。模块边界见 [核心服务说明](core/service/README.md)。
