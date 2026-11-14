# 构建与维护

[返回操作入口](README.md)

导入文档后需要建立两条链路：**图谱**（抽取节点与关系）与 **RAG**（切片与向量）。两者独立，可分别重建。

## 两类构建任务

```python
# 对已有文档建索引（可指定范围）
response = client.post("/api/v1/ingest", json={
    "paths": ["研究资料"],       # 目录或文件，相对路径
    "retry_failed": False,
    "mode": "all",              # all / graph / rag
})
job_id = response.json()["data"]["job_id"]

# 后台任务版（不阻塞，返回任务 ID）
client.post("/api/v1/jobs/ingest", json={"paths": ["研究资料"]})
client.post("/api/v1/jobs/summarize", json={"use_llm": True})
```

`POST /ingest` 会消耗模型额度（Embedding 与抽取）。只导入不建索引时用 `POST /import?ingest=false`。

查询进度：

```python
client.get("/api/v1/jobs")                      # 全部任务
client.get(f"/api/v1/jobs/{job_id}")            # 单个任务
response = client.post("/api/v1/build/retry-failed")   # 只重试失败链路
```

`/build/retry-failed` **只重试失败链路，不带入其他待处理文档**。

## 处理稿与重建

```python
client.get(f"/api/v1/documents/{source_id}/processed")
client.put(f"/api/v1/documents/{source_id}/processed", json={...})
client.post(f"/api/v1/documents/{source_id}/rebuild", json={"reprocess": False})
```

- `PUT /processed` 只保存文本，**旧索引保留至重建替换**。
- `POST /rebuild` 重建该文档的两条链路。
- `reprocess: true` 强制重新预处理（重新转换原文件），普通重建不重新预处理。

## 单文档重建 vs 全局重建

| 端点 | 范围 | 消耗 |
| --- | --- | --- |
| `POST /documents/{source_id}/rebuild` | 单个文档两条链路 | 该文档的模型调用 |
| `POST /maintenance/rebuild-knowledge-base` | 变化文档的知识库重建 | 只处理变化部分 |
| `POST /maintenance/rebuild-all` | **全项目影子重建** | 全量，最重 |

改图谱抽取档位后，下一次扫描会自动把已完成文档标记为 Graph 待重建；此时用 `rebuild-knowledge-base` 即可，**不必重建未变化的 RAG 向量**。

## 节点群整理

```python
response = client.post("/api/v1/maintenance/organize-graph", json={
    "summarize": True,
    "use_llm": True,
})
```

`organize-graph` 做三件事：合并重复节点、重算边权重与支持数、生成群组总结。

**这是 `/query/global` 的前置条件**——没有群总结时全局查询不调用模型。

注意其清理规则：删除 `ref_count == 0` 的节点、`support_count == 0` 的边、自环关系。这是"零引用不保留"的既定语义，手工补的节点若没有来源绑定也可能被清掉。

## 任务历史

```python
client.get("/api/v1/jobs")
```

返回维护任务列表，含状态、进度、结果与错误。任务持久化，重启后可查。

## 运行维护

```python
client.get("/api/v1/config")             # 读配置（含密钥字段已脱敏）
client.put("/api/v1/config", json={...}) # 保存完整配置
client.get("/api/v1/config/models")      # 上游可选模型目录
client.get("/api/v1/system/logs", params={"category": "internal", "limit": 200})
```

**这些是管理操作。自动化智能体不应擅自修改配置或运行维护动作**，除非用户明确要求。

`GET /system/logs` 的 `category` 取 `terminal`（Web 启停与 Python/Uvicorn）、`query`（检索生命周期与召回）、`internal`（导入、建库、维护），默认 `internal`。只读，不提供日志删除或任意文件读取。

## 更新

```python
client.get("/api/v1/update/status")    # 只读本地版本与工作区预检，不联网
client.post("/api/v1/update/check")    # 从公开仓库检查最新 SemVer
client.post("/api/v1/update/apply")    # 提交后台更新任务，仅本机可调用
```

`apply` 只允许来自 `localhost`、`127.0.0.1` 或 `::1`；程序文件有未提交修改时返回 `409 UPDATE_BLOCKED`。安装成功后 `restart_required` 为 `true`。

## Store 形态

绝对路径 Store 把上述操作全部镜像为 `POST`，`store_root` 放请求体：

```
POST /api/v1/stores/ingest
POST /api/v1/stores/maintenance/organize-graph
POST /api/v1/stores/maintenance/rebuild-knowledge-base
POST /api/v1/stores/maintenance/rebuild-all
POST /api/v1/stores/jobs/list
POST /api/v1/stores/jobs/get
```

## 并发纪律

构建期间写入类操作会返回 `409 PROCESSING` 或 `409 CONTENT_CONFLICT`。**等待后重试，不要并发重放**；也不要在后台构建进行中移动或删除资料。
