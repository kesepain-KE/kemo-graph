# kemo-graph 外部智能体 API

> **用途**：本文件定义 kemo-graph 对外提供给 kemo-agent、其他智能体或自动化程序的 HTTP API。
> **不包括**：Web 前端页面、React 路由、浏览器交互约定。
> **当前版本**：`1.0.0`
> **实现来源**：`api/__init__.py`、`api/routes.py`、`api/schemas.py`。

---

## 1. 服务与调用边界

### 1.1 独立启动

外部 API 可以不启动 Web 前端，直接启动独立 FastAPI 应用：

```powershell
cd E:\code\kemo-graph
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
- 单条关系删除当前**没有公开 HTTP API**；参见 [9. 已知能力边界](#9-已知能力边界)。

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
| 404 | `NOT_FOUND` | source_id、node_id 或路由不存在 | 先刷新状态/图谱确认 ID |
| 409 | `PROCESSING` | 知识库正在整理 | 等待后重试，不并发修改 |
| 413 | `FILE_TOO_LARGE` | 导入文件超过 50 MB | 拆分或压缩内容 |
| 415 | `UNSUPPORTED_FORMAT` | 不支持的文档扩展名 | 先转换为支持格式 |
| 422 | `CONVERSION_FAILED` | 文档无法转换为 Markdown | 检查文件是否损坏或加密 |
| 422 | `IMPORT_FAILED` | 导入过程失败 | 查看 message 与服务日志 |
| 502 | `INGEST_FAILED` | 转换成功，但图谱/RAG 整理失败 | 文档已保留，可稍后调用 ingest 重试 |
| 503 | `NOT_INITIALIZED` | 知识库尚未初始化 | 先导入或扫描至少一篇文档 |
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
查询文本切块 → Embedding → FAISS 候选召回 → Rerank → 阈值过滤
```

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

### 4.4 全局查询

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

### 4.5 搜索缓存与历史

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
```

返回：

```json
{
  "ok": true,
  "data": {
    "documents": [
      {
        "source_id": "source-uuid",
        "relative_path": "markdown/example-a1b2c3.md",
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
    }
  },
  "error": null
}
```

建议智能体删除或读取内容前，先调用本端点取得 `source_id`。

---

### 5.2 读取转换后的 Markdown 内容

```http
GET /api/v1/documents/{source_id}/content
```

返回 `source_id`、`relative_path` 与 `content`。内容是知识库实际使用的 Markdown，而不是原始 PDF/DOCX 二进制文件。

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

支持扩展名：

```text
.pdf, .docx, .md, .markdown, .txt, .html, .htm, .rst, .csv
```

最大文件：**50 MB**。

PowerShell 示例：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/import?ingest=false" `
  -Form @{ file = Get-Item "E:\documents\example.pdf" }
```

成功响应：

```json
{
  "ok": true,
  "data": {
    "source_id": "source-uuid",
    "original_filename": "example.pdf",
    "detected_format": "pdf",
    "markdown_relative_path": "markdown/example-a1b2c3d4.md",
    "conversion_status": "completed",
    "ingest_status": "pending",
    "size": 123456
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

---

### 5.4 兼容的纯 Markdown 文本上传

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

该端点只适合智能体已有 Markdown 正文时使用：写入并注册为待整理文档，不会在此请求中调用模型。文件名不能含 `/`、`\\` 或 `..`。

对于 PDF、DOCX 等二进制文件，必须使用 `/import`。

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
| `paths` | string[] / null | 否 | 要整理的 Markdown 相对路径；`null` 表示扫描所有待处理文档 |
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
Markdown → external/recycle/
sources.exists_status → deleted
删除该 source 的 node_sources / edge_sources / chunks / embeddings / chunk_nodes
同步删除 FAISS 向量
重算仍有来源的节点引用数和边权重
```

响应包含：

```text
deleted_source_id
relative_path
recycled_path
graph_deleted
rag_deleted
```

---

## 6. 图谱节点 API

### 6.1 删除节点

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
      若该 source 没有任何其他节点 → Markdown 移入回收站，并级联删除其 Graph/RAG/FAISS 数据
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
    "recycled_files": ["recycle/markdown/example.md"],
    "unlinked_files": ["markdown/another.md"]
  },
  "error": null
}
```

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

请求体是完整配置对象。配置由服务端校验并原子写入。

**风险**：这是全量配置更新，不是 JSON Patch。调用方必须先 `GET /config`，只修改必要字段后带回完整对象；不要自行编造或删除未知配置项。

密钥优先级：非空 `kemo.api_key` > `kemo.api_key_env` 指定的环境变量。把掩码原样提交会保留现有显式密钥；把字段清空并保存会删除显式密钥并回退到环境变量。`kemo.api_key_source` 和 `kemo.protocol_version` 是 Web 只读字段。

### 7.3 手动生成节点群总结

```http
POST /api/v1/maintenance/summarize
```

只有累计变动文件数量达到 `summary_trigger_file_count` 时才会真正生成总结；否则返回跳过原因。该动作可能调用图谱 LLM。

### 7.4 清理过期回收站

```http
POST /api/v1/maintenance/cleanup-recycle
```

按回收站 `.meta.json` 的 `expires_at` 永久删除到期文件；不可恢复。

### 7.5 永久清空回收站

```http
DELETE /api/v1/maintenance/recycle
```

强制删除回收站中的全部文件与元数据，不考虑到期时间。该操作不可恢复，必须由用户明确确认。

### 7.6 后台整理文档

```http
POST /api/v1/jobs/ingest
Content-Type: application/json

{"paths": null, "mode": "both"}
```

与同步 `/ingest` 使用相同参数，但立即返回 job。网页导入完成后使用此端点，避免页面切换或请求连接中断影响长任务。

### 7.7 知识图谱整理

```http
POST /api/v1/maintenance/organize-graph
Content-Type: application/json

{"use_llm": true, "summarize": true}
```

只整理 Graph 投影：合并已检查的重叠节点/关系、迁移来源事实、重算权重和 `chunk_nodes`。不重读 Markdown，不调用 Embedding。

### 7.8 变化文档知识库重建

```http
POST /api/v1/maintenance/rebuild-knowledge-base
```

重试新增、变化、删除和失败文档；未变化文档按哈希跳过。

### 7.9 全项目影子重建

```http
POST /api/v1/maintenance/rebuild-all
```

在影子目录从全部活动 Markdown 重建 Graph、RAG、FAISS 和派生关联。校验通过才切换正式目录，并在 `data/` 同级保留旧知识库备份；失败不改变正式库。

### 7.10 查询后台任务

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

---

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

### 9.1 单条关系线删除未对外暴露

虽然内部图谱工具层存在单条关系删除能力，但当前 HTTP API **没有**：

```text
DELETE /api/v1/relations/{edge_id}
```

CLI 和 Web 前端也没有单条关系删除入口。

目前如需消除关系，只能：

- 删除产生该关系证据的源文档；或
- 删除关系关联的节点（会删除该节点全部关系）；或
- 后续新增正式的关系删除 API。

### 9.2 不支持远程 URL 导入

`/import` 只接收上传的本地文件，不接收 URL。调用方若持有网络 URL，应先自行安全下载、校验后再上传。

### 9.3 不支持恢复回收站文件

用户主动删除的文档或节点关联独占文档会移入 `external/recycle/`，但当前没有恢复 API。到期清理后永久删除。

---

## 10. 运行与日志

运行日志默认按 UTC 日期写入：

```text
E:\code\kemo-graph\log\YYYY-MM-DD.tsv
```

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
