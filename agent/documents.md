# 文档与目录

[返回操作入口](README.md)

知识库按**真实文件目录**管理原始资料，处理稿存放在独立目录并映射回原文件。目录可任意嵌套，没有"项目""共享""用户"这类分组。

| 路径 | 内容 |
| --- | --- |
| `external/originals/` | 原文件 |
| `external/markdown/` | 规范 Markdown 处理稿 |

自定义 Markdown 路径时，原文件目录位于其同级 `originals/`。

## 列出文档

```python
response = client.get("/api/v1/documents", params={
    "status": "active",       # active / pending / all
    "page": 1,
    "page_size": 20,
})
```

可用筛选：`directory`、`search`、`graph_status`、`rag_status`、`include_summary`。

## 读原文与位置

```python
client.get(f"/api/v1/documents/{source_id}/content")    # 原始正文
client.get(f"/api/v1/documents/{source_id}/original")   # 原文件
client.get(f"/api/v1/documents/{source_id}/source")     # 位置信息
client.get(f"/api/v1/documents/{source_id}/processed")  # 处理稿
```

改原始正文用 `PUT /content`；改处理稿用 `PUT /processed`。

**编辑处理稿不等于重建索引**：`PUT /processed` 只保存文本，旧索引保留至重建替换。要同时生效用 `POST /documents/{source_id}/rebuild`。

注意处理稿保存对 `expected` 内容有校验，篡改会返回 `409 CONTENT_CONFLICT`，需重读后再改。

## 导入

```python
with open("资料.pdf", "rb") as handle:
    response = client.post(
        "/api/v1/import",
        files={"file": ("资料.pdf", handle, "application/pdf")},
        params={"ingest": "false", "directory": "研究资料"},
    )
```

- 只接收**上传的本地文件**，不接受 URL。持有 URL 时应先自行下载校验再上传。
- 目标目录必须已存在。
- `ingest=false` 只保存原文件并转换，不消耗模型额度；随后再用 `POST /ingest` 建索引。
- 同目录下同名但内容不同的导入会返回冲突。

另有 `POST /upload`（原始文本上传）与 `POST /ingest`（对已有文档建图谱与向量）。

## 目录操作

```python
client.post("/api/v1/library/entries", json={
    "directory": "研究资料", "name": "笔记.md", "kind": "file",   # 新建文档；文件夹用 kind="directory"
})
client.patch("/api/v1/library/entries", json={
    "path": "研究资料/笔记.md", "destination": "归档/笔记.md",     # 移动或重命名
})
client.post("/api/v1/library/transfer", json={
    "path": "研究资料/笔记.md", "directory": "归档",
    "mode": "copy", "conflict": "error",                        # 剪切用 mode="cut"
})
client.delete("/api/v1/library/entries", json={"path": "归档/笔记.md"})
client.post("/api/v1/library/refresh", json={"path": ""})        # 扫描磁盘变化
client.post("/api/v1/library/build", json={"path": "研究资料", "reprocess": False})
```

`GET /api/v1/library` 的 `data` 含 `root`（原文件根目录绝对路径）、`directory`（当前相对目录）、`tree`（完整递归树）、`entries`（当前目录第一层；搜索时为全库匹配结果）。

**改名或移动会保留 `source_id`、处理稿路径和已有索引**，只更新来源映射，不调用模型。刷新会发现磁盘新增、修改与删除并重新生成变化的处理稿；刷新本身也不调用模型。

## 预览

`GET /api/v1/library/preview` 与 `GET /api/v1/library/original` 提供原文件预览所需内容。原文件预览与图谱使用的 Markdown 转换是**两套独立实现**——预览要内联图片与样式，索引转换则会清成占位符。

## 删除

```python
client.delete(f"/api/v1/documents/{source_id}")
client.post("/api/v1/documents/delete-batch", json={"source_ids": ["a", "b"]})
client.delete("/api/v1/documents")     # 清空当前库全部文档
```

**删除不可恢复**：同时移除原文件、处理稿及对应 Graph/RAG 数据，并清空相关搜索缓存。

若返回 `503 SEARCH_CACHE_CLEANUP_FAILED`，说明数据变更已生效但缓存清理失败：用 `error.result` 里的 ID 复核已提交结果，再单独调用 `DELETE /api/v1/search/cache`，**不要重放删除**。

后台构建进行中请等待完成再移动或删除资料。

## Portable Store 形态

绝对路径 Store 使用 `content/originals/` 与 `content/markdown/`，全部走 `POST` 且 `store_root` 放在 JSON 请求体里：

```
POST /api/v1/stores/library/list
POST /api/v1/stores/library/create
POST /api/v1/stores/library/move
POST /api/v1/stores/library/delete
POST /api/v1/stores/library/refresh
```

## 旧行为已移除

旧项目组织 API（`/projects`、`move-batch`）已删除，调用方需改用目录 API。仅保留处理后 Markdown 的旧版资料不会被伪装成原文件——升级后需重新导入原始资料才能获得原格式预览。
