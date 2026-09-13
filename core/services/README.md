# KnowledgeBase 领域服务

`core.knowledge_base.KnowledgeBaseService` 是 CLI、HTTP API 与 Web 共用的
兼容门面。外部调用不需要改为直接依赖领域服务；门面会把入口委托到本目录
的服务对象，并保留原有方法签名和返回结构。

| 服务 | 负责入口 | 当前实现策略 |
| --- | --- | --- |
| `DocumentService` | 文档列表、项目文件夹、重命名/移动、内容读写、导入、上传、外部来源同步、精确/批量删除 | 项目组织由 `core/document_organization.py` 承载；其余复杂写事务暂由门面中的 `*_impl` 承载 |
| `GraphService` | 节点、关系、全量图、可视化分页与局部邻域 | 可视化分页/邻域直接在服务中调用图谱数据层；节点/关系写操作暂由 `*_impl` 承载 |
| `RetrievalService` | Graph、RAG、Hybrid、Answer、Global 检索与搜索缓存 | 查询门面委托；缓存状态指纹、锁、读写和降级逻辑已迁入服务 |
| `MaintenanceService` | 状态/配置、图谱整理、节点群总结、知识库重建、任务与回收站 | 入口已按域集中；重建与总结事务暂由 `*_impl` 承载 |

服务通过 `ServiceOwner` Protocol 获取最小运行时上下文，不反向导入
`KnowledgeBaseService`，因此不会形成循环依赖。后续要继续下沉实现时，优先
按同一服务的 `*_impl` 迁移，并为每个事务补充领域测试；不要让 `api/routes.py`
或 `start.py` 直接调用底层 SQLite、FAISS 或缓存模块。

## 读取缓存与检索进度

`core/read_cache.py` 是进程内有界读取缓存的统一入口，应用于文档分页、运行状态、知识库指纹、日志和配置等读取，不缓存可变的服务实例或数据库连接。容量、文件变化校验和并发读取合并由该模块负责，持久化搜索结果仍由 `core/search_cache.py` 管理。

`core/query_progress.py` 通过请求上下文记录真实检索步骤；`api/query_progress.py` 负责可选请求头、内存记录与只读轮询接口。检索组件只调用步骤钩子，不依赖 HTTP 请求或前端。未绑定进度上下文时钩子不记录数据，CLI 和已有调用签名保持不变。
