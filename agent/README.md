# 智能体操控知识图谱

本目录是 **kemo-graph 2.0.0 的智能体操作手册**，供任何第三方智能体在用户授权后操控知识图谱服务，不依赖特定智能体框架。用户向智能体提供图谱服务根地址与操作目标；智能体使用 HTTP 客户端或网络工具，按本手册完成检索、导入、图谱维护与读取，网页中能看到相同结果。开始前读 [连接与服务边界](connection.md)，再只读当前任务对应的手册。

| 用户要做的事 | 操作手册 |
| --- | --- |
| 连接服务、确认状态、理解响应包络与错误码 | [连接与服务边界](connection.md) |
| 回答问题、找资料、取证据、区分五种检索模式 | [检索与问答](query.md) |
| 列出文档、读原文、导入文件、改处理稿、建目录 | [文档与目录](documents.md) |
| 读图谱、按节点做局部 BFS、搜节点、创建节点与关系、删除 | [图谱读写](graph.md) |
| 触发构建、重试失败、整理节点群、重建、查后台任务 | [构建与维护](build.md) |
| 查所有端点路径、方法与用途 | [端点速查](endpoints.md) |

## 完成一次操作

1. 先 `GET /api/v1/status` 确认知识库已初始化、文档数量与 Graph/RAG 状态。空库不会已有图谱或向量索引。
2. 用真实 `source_id` / `node_id` / `edge_id`。不要根据关键词自造 ID；先查列表或搜索取得。
3. 所有响应都是 `{ok, data, error}` 包络。先判 `ok`，再读 `data`；失败时读 `error.code` 决定是否重试。
4. 写入类操作（创建、删除、导入、构建）会校验写锁。返回 `409 PROCESSING` 或 `409 CONTENT_CONFLICT` 表示库里正有构建在跑，等待后重试，不要并发重放。
5. 删除是不可恢复的。删除前先读详情核对来源与影响范围；共享来源的节点会在删除时级联处理，不要假设只影响一个文件。
6. 向用户说明改了哪些对象、结果如何、生效方式，以及仍需用户确认的信息。

检索端点对相同问题会命中搜索缓存；需要重新执行时传查询参数 `force=true`。

## 常用任务顺序

**回答一个需要依据的问题**：`GET /status` 确认就绪 → `POST /query/hybrid` 拿图谱关系 + 原文片段 + 实体群组 → 若证据不足再 `POST /query/rag` 补语义召回 → 需要模型组织答案时用 `POST /query/answer`（返回 `retrieval` 供审计）→ 引用时带上 `source.relative_path`。

**只想知道某概念周围有什么**：`GET /graph/search` 找到 `node_id` → `GET /graph/neighborhood/{node_id}` 拿确定性 BFS 局部图谱（不调 LLM）→ 按 `truncated` / `edges_truncated` 判断是否要缩小 `depth`。

**导入新资料并建立索引**：`GET /library` 确认目标目录存在 → `POST /import`（本地文件，不接受 URL）→ `POST /ingest` 建图谱与向量 → `GET /jobs/{job_id}` 看进度 → 需要时 `GET /documents/{source_id}/processed` 读处理稿。

**手工补一条概念或关系**：`POST /nodes` 创建节点（**不幂等**，同名不合并）→ `POST /relations` 连边（按三元组去重，同来源覆盖证据）→ 若要参与向量召回，需另跑既有向量同步流程。

**删掉一份资料**：`GET /documents/{source_id}` 读详情 → 确认没有其他资料依赖该节点 → `DELETE /documents/{source_id}` → 若返回 `503 SEARCH_CACHE_CLEANUP_FAILED`，先用 `error.result` 里的 ID 复核已提交结果，再单独清理搜索缓存，不要重放删除。

## 文档位置

智能体操控图谱的说明统一维护在本目录。开始前的连接与错误处理见 [连接与服务边界](connection.md)；完整请求字段、响应结构与全部错误码见 [`api.md`](../api.md)；安装、网页使用与配置见 [项目介绍](../README.md)。

## 当前能力边界

- `/import` 只接收上传的本地文件，不接收远程 URL。持有 URL 时应先自行下载校验再上传。
- 文件删除与节点删除不可恢复，会同时移除对应索引。
- `POST /nodes`、`POST /relations` 不调用模型、不自动生成实体向量。新节点要参与 Hybrid 的 `entities` 召回，需经既有向量同步流程补齐索引。
- 创建能力暂无 `/stores/*` 镜像、批量创建、全节点字段修改或关系修改。
- 外部 API 未实现应用层鉴权，默认只应监听 `127.0.0.1`。
