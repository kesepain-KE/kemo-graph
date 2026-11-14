# 连接与服务边界

[返回操作入口](README.md)

## 先确认服务形态

服务根地址形如 `http://127.0.0.1:8000`，所有业务端点位于 `/api/v1` 之下，例如 `http://127.0.0.1:8000/api/v1/status`。

两种启动形态的差别会直接影响可用端点：

| 启动方式 | 前端网页 | `system/runtime` 与 `system/restart` |
| --- | --- | --- |
| `uvicorn api:app --host 127.0.0.1 --port 8000` | 不挂载 | **不注册** |
| `python start_web.py` | 挂载 | 额外注册 |

只依赖知识库能力时用纯 API 形态即可；需要网页控制台或重启能力时才用 `start_web.py`。不要因为 OpenAPI 里缺少 `system/*` 就判断服务端未实现。

## 未实现应用层鉴权

当前外部 API **没有内建鉴权**，因此默认只应监听回环地址 `127.0.0.1`。删除、导入、配置和维护端点都能修改本地知识库数据，不要把该端口直接暴露到公网或不可信局域网。

跨设备访问的正确做法是在外部部署 VPN、反向代理、TLS 或认证层，而不是改监听地址了事。

## 读取状态

```python
import httpx

client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30)

response = client.get("/api/v1/status")
response.raise_for_status()
state = response.json()["data"]
```

`GET /api/v1/status` 返回初始化状态、文档数量、Graph/RAG 状态与 FAISS 健康状态。**空库不会已有图谱或向量索引**，不要假设索引一定存在。

## 响应包络

所有端点统一返回：

```json
{ "ok": true, "data": {}, "error": null }
```

失败时 `ok=false`、`data=null`，`error` 至少含 `code` 与 `message`：

```json
{
  "ok": false,
  "data": null,
  "error": { "code": "INVALID_PARAM", "message": "具体错误说明" }
}
```

调用方应先判 `ok` 再读 `data`，不要直接按成功结构解析。

## 常见错误码

| HTTP | error.code | 含义 | 处理建议 |
|---:|---|---|---|
| 400 / 422 | `INVALID_PARAM` | 参数、请求体或业务前置条件非法 | 修正参数后再试 |
| 400 | `INVALID_PATH` | 导入路径不安全或上传文件名含目录 | 用安全文件名或修正本地路径 |
| 404 | `NOT_FOUND` | `source_id`、`node_id`、`edge_id` 或路由不存在 | 先刷新列表或搜索确认 ID |
| 409 | `PROCESSING` | 知识库正在整理 | 等待后重试，不并发修改 |
| 409 | `CONTENT_CONFLICT` | 其他写操作持有写锁 | 等待后重试 |
| 409 | `GRAPH_CHANGED` | 分页携带的 `expected_revision` 已过期 | 重读 `visualization/meta` 再加载 |
| 409 | `IMPORT_SOURCE_CHANGED` | 源文件 SHA-256 与调用方确认的不一致 | 重新扫描后再导入 |
| 413 | `FILE_TOO_LARGE` | 导入文件超过 50 MB，或转换结果超过 20,000,000 字符 | 拆分或精简 |
| 415 | `UNSUPPORTED_FORMAT` | 没有对应 Converter | 先转换格式 |
| 422 | `CONVERSION_FAILED` | 无法转换为 Markdown，或缺少格式依赖 | 检查 message 里的依赖名 |
| 422 | `IMPORT_FAILED` | 导入过程失败 | 查看 message 与服务日志 |
| 502 | `INGEST_FAILED` | 转换成功但图谱/RAG 整理失败 | 文档已保留，可稍后重试 ingest |
| 503 | `NOT_INITIALIZED` | 知识库尚未初始化 | 先导入或扫描至少一篇文档 |
| 503 | `SEARCH_CACHE_CLEANUP_FAILED` | 数据变更已生效但搜索缓存清理失败 | 用 `error.result` 复核已提交结果，再单独清缓存；**不要重放变更** |
| 500 | `INTERNAL` | 未预期内部错误 | 查看运行日志 |

## 请求约定

- JSON 请求使用 `Content-Type: application/json`。
- 文档导入端点使用 `multipart/form-data`。
- **所有 JSON 请求模型拒绝未声明字段**（`extra="forbid"`），不要传额外参数，否则返回 `422 INVALID_PARAM`。
- 图谱或 RAG 正在构建时，依赖该数据的读取/修改请求会返回 `409 PROCESSING`，调用方应等待后重试。

## 一个 OpenAPI 不可见的兼容别名

`GET /api/v1/graph/full` 设置了 `include_in_schema=False`，因此不出现在 OpenAPI `paths` 中，但路由表里**真实存在**，行为等同 `GET /api/v1/graph`。新调用方应使用 `/graph`；不能因为 OpenAPI 缺少该别名就判断完整图谱读取未实现。
