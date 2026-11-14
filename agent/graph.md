# 图谱读写

[返回操作入口](README.md)

图谱有三类数据：节点（`node`）、关系（`edge`）、节点群（`group`）。**群组完全由整理流程生成，没有写接口。**

## 读整个图谱

```python
response = client.get("/api/v1/graph", params={"nodes_page": 1, "nodes_page_size": 100})
graph = response.json()["data"]
```

不传分页参数返回全部节点；传 `nodes_page` 时**只对节点分页，关系线与群总结仍全量返回**。

`data` 含 `nodes`、`edges`、`groups`、`nodes_pagination`。节点形如：

```json
{
  "node_id": "node-uuid", "keyword": "知识图谱", "summary": "……",
  "aliases": ["KG"], "tags": ["AI"], "ref_count": 2,
  "created_at": "…", "updated_at": "…"
}
```

关系形如：

```json
{
  "edge_id": "edge-uuid", "source_node_id": "node-a",
  "relation": "包含", "target_node_id": "node-b",
  "weight": 0.9, "support_count": 1, "created_at": "…"
}
```

读整图只为拿 ID、审查可删目标、看群总结。**不要把它当成检索手段**。

## 按节点做局部 BFS

```python
response = client.get(f"/api/v1/graph/neighborhood/{node_id}", params={
    "depth": 2,
    "direction": "both",     # forward / backward / both
    "limit": 2000,           # 节点上限，最大 10000
    "edge_limit": 10000,     # 关系上限，最大 50000
})
```

`depth` 取 1–6。该端点**直接按 `node_id` 执行数据库 BFS，不调用 LLM、Embedding 或 Rerank**，是本服务里最便宜的图谱读取方式。

响应节点带 `depth` 字段，并用 `truncated` / `edges_truncated` 表示是否触到安全上限——**触到上限时要缩小 `depth` 或范围重新取，不要当完整结果用**。

## 搜索节点

```python
response = client.get("/api/v1/graph/search", params={"query": "知识图谱", "limit": 20})
```

按 `keyword` 与 `aliases` 搜索。创建节点前**必须先搜**，避免重复。

## 可视化分页

节点与关系分别分页，避免节点翻页时重复传输全部关系：

```python
meta = client.get("/api/v1/graph/visualization/meta").json()["data"]
revision = meta["revision"]

client.get("/api/v1/graph/visualization/nodes", params={
    "page": 1, "page_size": 1000, "expected_revision": revision,
})
client.get("/api/v1/graph/visualization/edges", params={
    "page": 1, "page_size": 2000, "expected_revision": revision,
})
```

`meta` 返回内容派生的 64 位 `revision`、节点/关系/群组数量与群组摘要。节点页额外返回每个节点的 `source_ids` 与 `group_ids`。

**必须先读 `meta`，再把该值作为 `expected_revision` 请求各页**。加载期间图谱发生变化时返回 `409 GRAPH_CHANGED`，需重读 `meta` 后重新加载。

## 创建节点

```python
response = client.post("/api/v1/nodes", json={
    "keyword": "知识图谱",
    "summary": "该概念在当前语境中的完整描述",
    "aliases": ["KG"],
    "tags": ["AI"],
    "source_id": "manual",
    "content_hash": "manual",
})
node = response.json()["data"]      # 直接返回节点，不包 node
node_id = node["node_id"]
```

| 字段 | 必填 | 约束 |
| --- | :---: | --- |
| `keyword` | 是 | 去除首尾空白后 2–20 字符 |
| `summary` | 是 | 去除首尾空白后 1–200000 字符 |
| `aliases` / `tags` | 否 | 字符串数组，默认 `[]`，按首次出现顺序去重 |
| `source_id` | 否 | 默认 `"manual"`，1–200 字符 |
| `content_hash` | 否 | 默认 `"manual"`，不能为空 |

**不幂等**：每次 POST 都创建独立节点，同名 `keyword` **不自动合并**。重复调用会产生重复节点。

创建时会写入来源绑定，`ref_count` 为 1；不需要在文档来源表里伪造实体文件记录。后续图谱整理不会因零引用清除该节点，但**不保证同名节点整理后仍保持独立**。

创建后立即可通过节点详情、`GET /graph`、可视化和 `/graph/search` 读取。

**不调模型**：不生成实体向量、节点群或群总结。底层有单节点 `build_entity_vectors(node_id, summary)` 与批量 `sync_entity_vectors(...)`；只有经这些既有流程补齐索引后，新节点才参与 Hybrid 的 `entities` 语义召回。

## 创建关系

```python
response = client.post("/api/v1/relations", json={
    "source_node_id": "node-a",
    "relation": "增强",
    "target_node_id": "node-b",
    "evidence_weight": 0.9,
    "source_id": "manual",
    "content_hash": "manual",
})
edge = response.json()["data"]      # 直接返回关系，不包 edge
```

源与目标节点必须已存在，否则 `404 NOT_FOUND`。`relation` 为 1–100 字符。`evidence_weight` 必填，0–1 的有限数字，**不接受布尔值或数字字符串**。

**去重与证据语义**：

- 按 `(source_node_id, relation, target_node_id)` 去重，重复提交返回同一 `edge_id`，不新建重复边。
- 同一关系、同一 `source_id` 再次提交会**覆盖**该来源的 `content_hash` 与 `evidence_weight`，不增加证据条数。
- 不同 `source_id` 增加来源证据；`support_count` 为当前来源数，`weight` 为**当前所有来源证据的最大值**，不是历史最大值。

例如同来源权重 `0.9 → 0.2` 后只剩一条证据，边权重变为 `0.2`；再新增另一来源 `0.7`，支持数为 2、边权重为 `0.7`。

## 读单个对象

```python
client.get(f"/api/v1/nodes/{node_id}")       # 节点 + 双向关系 + 来源绑定
client.get(f"/api/v1/relations/{edge_id}")   # 关系详情
```

删除前**先读详情**核对来源与影响范围。

## 修改节点摘要

```python
client.patch(f"/api/v1/nodes/{node_id}", json={
    "summary": "新的完整描述",
    "expected_summary": "读取时拿到的旧摘要",
})
```

带 `expected_summary` 乐观校验：期间被他人改过会返回 `409 CONTENT_CONFLICT`，需重读后再改。

**当前只能改 `summary`**。改名、改别名、改标签在 HTTP 层没有入口。

## 删除

```python
client.delete(f"/api/v1/nodes/{node_id}")
client.delete(f"/api/v1/relations/{edge_id}")
```

节点删除会按既有规则级联处理：解除来源绑定，失去全部来源时删除节点与关联边；独占文档也可能一并清理。**先读详情确认，不要当成本地无害操作。**

两者都会清空搜索缓存。

## 写入后的副作用

两个创建端点都使用知识库写锁与 SQLite 事务；成功后刷新图谱统计、递增 `changed_since_summary`、清理搜索缓存。

`revision` 由权威数据派生，创建节点或变更关系后携带旧 `expected_revision` 的可视化分页会返回 `409 GRAPH_CHANGED`。

若返回 `503 SEARCH_CACHE_CLEANUP_FAILED`：写入已提交但清缓存失败。用 `error.result` 里的已提交节点或关系核对后再处理缓存故障，**不要盲目重试创建**。

## 当前能力边界

- 无 `/stores/*` 镜像端点
- 无批量创建
- 无全字段节点修改（只能改 `summary`）
- **无关系修改**：关系名或权重写错只能删边重建，而删边会丢该边全部证据
- 无指定两节点合并；合并只能经 `maintenance/organize-graph` 整体整理
- 无手工维护群组
