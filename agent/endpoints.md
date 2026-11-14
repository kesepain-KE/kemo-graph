# 端点速查

[返回操作入口](README.md)

所有端点以 `/api/v1` 为前缀。`{source_id}`、`{node_id}`、`{edge_id}`、`{job_id}`、`{cache_key}` 为路径参数，需用真实 ID。

## 状态与配置

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/status` | 知识库概览、Graph/RAG 状态、FAISS 健康 |
| `GET` | `/config` | 读配置（密钥字段脱敏） |
| `PUT` | `/config` | 保存完整配置 |
| `GET` | `/config/models` | 上游可选模型目录 |
| `GET` | `/system/logs` | 只读运行日志（`category`=terminal/query/internal） |

## 检索与问答

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/query/graph` | 图谱查询：命中节点、关系扩展、多跳路径 |
| `POST` | `/query/rag` | 向量查询：切片语义召回与重排序 |
| `POST` | `/query/hybrid` | 混合查询：图谱增强的切片召回 |
| `POST` | `/query/answer` | 混合检索 + LLM 组织答案 |
| `POST` | `/query/global` | 群组级整体回答 |
| `GET` | `/query/progress/{progress_id}` | 检索步骤进度 |
| `GET` | `/search/cache` | 搜索缓存列表 |
| `GET` | `/search/cache/{cache_key}` | 缓存详情（含完整 result） |
| `DELETE` | `/search/cache` | 清理缓存（`stale_only=true` 只删过期） |

## 文档

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/documents` | 列出文档 |
| `DELETE` | `/documents` | 删除当前库全部文档 |
| `POST` | `/documents/delete-batch` | 批量删除 |
| `DELETE` | `/documents/{source_id}` | 删除单个文档 |
| `GET` | `/documents/{source_id}/content` | 读原始正文 |
| `PUT` | `/documents/{source_id}/content` | 写原始正文 |
| `GET` | `/documents/{source_id}/processed` | 读处理稿 |
| `PUT` | `/documents/{source_id}/processed` | 写处理稿（不重建索引） |
| `POST` | `/documents/{source_id}/rebuild` | 重建该文档两条链路 |
| `GET` | `/documents/{source_id}/original` | 取原文件 |
| `GET` | `/documents/{source_id}/source` | 取来源位置 |
| `POST` | `/import` | 上传本地文件并导入（**不接受 URL**） |
| `POST` | `/upload` | 上传原始文本 |
| `POST` | `/ingest` | 对已有文档建图谱与向量 |

## 目录

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/library` | 目录树与当前目录内容 |
| `POST` | `/library/entries` | 新建文件夹或文档 |
| `PATCH` | `/library/entries` | 移动或重命名 |
| `DELETE` | `/library/entries` | 删除文件/文件夹并清索引 |
| `POST` | `/library/transfer` | 复制或剪切到目标目录 |
| `POST` | `/library/refresh` | 扫描磁盘变化并更新处理稿 |
| `POST` | `/library/build` | 构建指定文件或目录 |
| `GET` | `/library/preview` | 原文件预览 |
| `GET` | `/library/original` | 取原文件 |

## 图谱

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/graph` | 完整图谱（节点可单独分页） |
| `GET` | `/graph/search` | 按关键词/别名搜节点 |
| `GET` | `/graph/neighborhood/{node_id}` | 确定性 BFS 局部图谱（不调模型） |
| `GET` | `/graph/visualization/meta` | 拿 `revision` 与统计 |
| `GET` | `/graph/visualization/nodes` | 节点分页 |
| `GET` | `/graph/visualization/edges` | 关系分页 |
| `POST` | `/nodes` | **创建节点**（不幂等，同名不合并） |
| `GET` | `/nodes/{node_id}` | 节点详情 + 来源绑定 |
| `PATCH` | `/nodes/{node_id}` | 改节点摘要（带乐观校验） |
| `DELETE` | `/nodes/{node_id}` | 删除节点（级联） |
| `POST` | `/relations` | **创建关系**（按三元组去重） |
| `GET` | `/relations/{edge_id}` | 关系详情 |
| `DELETE` | `/relations/{edge_id}` | 删除关系 |

> `GET /graph/full` 是兼容别名，`include_in_schema=False`，不出现在 OpenAPI 但在路由表中真实存在。

## 构建与维护

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/jobs/ingest` | 后台建索引任务 |
| `POST` | `/jobs/summarize` | 后台总结任务 |
| `GET` | `/jobs` | 任务列表 |
| `GET` | `/jobs/{job_id}` | 任务详情与进度 |
| `POST` | `/build/retry-failed` | **只**重试失败链路 |
| `POST` | `/maintenance/organize-graph` | 合并去重、重算权重、生成群总结 |
| `POST` | `/maintenance/summarize` | 生成节点群总结 |
| `POST` | `/maintenance/rebuild-knowledge-base` | 变化文档的知识库重建 |
| `POST` | `/maintenance/rebuild-all` | 全项目影子重建（最重） |

## 更新

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/update/status` | 本地版本与工作区预检（不联网） |
| `POST` | `/update/check` | 检查远端最新版本 |
| `POST` | `/update/apply` | 提交更新任务（仅本机） |

## 绝对路径 Store

全部为 `POST`，`store_root` 放在 JSON 请求体，用于在任意绝对路径建立独立知识库。

**生命周期与导入**：`/stores/initialize`、`/stores/info`、`/stores/status`、`/stores/upload`、`/stores/import`、`/stores/import-path`、`/stores/ingest`、`/stores/sources/{sync,status,delete}`

**检索与回答**：`/stores/query/{graph,rag,hybrid,answer,global,federated}`

**文档**：`/stores/documents/{list,content,update,delete,delete-batch,delete-all}`

**目录**：`/stores/library/{list,create,move,delete,refresh}`

**图谱**：`/stores/nodes/{get,delete}`、`/stores/relations/{get,delete}`、`/stores/graph/{full,neighborhood,visualization/meta,visualization/nodes,visualization/edges}`

**搜索缓存**：`/stores/cache/{list,show,clear}`

**维护与任务**：`/stores/maintenance/{organize-graph,summarize,rebuild-knowledge-base,rebuild-all}`、`/stores/jobs/{list,get}`

> Store 形态目前**没有**节点/关系创建端点，也没有批量创建。

## 只在 `start_web.py` 下存在

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/system/runtime` | 当前 PID 与运行状态 |
| `POST` | `/system/restart` | 底层重启（需 `{"confirm": "restart"}`） |

纯 `uvicorn api:app` 形态**不注册**这两个端点。
