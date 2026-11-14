# 核心服务与模块边界

应用通过 `from core import KnowledgeBaseService` 或 `from core.service import KnowledgeBaseService` 获取统一服务。根入口提供 24 个常用符号，按访问惰性导出，不读取运行配置、不创建应用实例；这也避免 provider 先导入时形成循环。

`KnowledgeBaseService` 保留原有公开方法和参数，每个公开方法最多三条语句，只做一次领域委托。`ServiceContext` 保存配置、路径、日志和引擎工厂；`DocumentService`、`GraphService`、`RetrievalService`、`MaintenanceService` 持有真实实现，不再反向调用门面的 `_xxx_impl`。

## 依赖层序

| 层 | 包 | 职责 |
| --- | --- | --- |
| 0 | `core.common` | 时间、原子写入、文本、路径、SQL、读缓存；只依赖标准库 |
| 1 | `core.settings`、`core.observability` | 配置、日志、查询进度 |
| 2 | `core.storage` | SQLite 连接、schema、meta、锁、存储清单 |
| 3 | `core.documents`、`core.graph`、`core.rag` | 原文件与处理稿映射、转换与共享切分、图谱、向量 |
| 4 | `core.ingest`、`core.retrieval` | 构建编排、混合查询与搜索缓存 |
| 5 | `core.maintenance` | 后台任务、重建、定时维护、同步 |
| 6 | `core.service` | 统一服务与跨存储装配 |

依赖只能指向同层或更低层。下层不能导入 `core.service`，`core` 不能导入 `api` 或 `provider.tools`。provider 的删除工具按调用需要导入构建编排，不在模块顶层导入它。

## 公开入口与实现位置

常用类型和入口从所属包门面导入。细分能力沿稳定的领域模块路径定位，例如 `core.documents.library`、`core.documents.conversion`、`core.graph.visualization`、`core.service.federation`；测试在验证内部行为时可以导入对应实现模块。旧的 `core.config`、`core.db`、`core.knowledge_base`、`core.ingestor` 等兼容路径已删除。

| 包门面 | 导出上限 |
| --- | ---: |
| `core` | 24 |
| `core.common`、`core.graph` | 24 |
| `core.settings`、`core.documents`、`core.service` | 20 |
| `core.storage`、`core.rag`、`core.ingest` | 16 |
| `core.observability`、`core.retrieval` | 12 |
| `core.maintenance` | 8 |

预算按现有公开契约校正，不删减原有异常和方法。子包门面只做显式导入与 `__all__`；根门面使用 PEP 562 按需解析导出，不装配运行时。

API 路由位于 `api/routes/`，请求与响应模型位于 `api/schemas/`。路由聚合顺序由 `api/routes/__init__.py` 明确维护，每个路由模块不超过 250 行。`api.create_app()` 显式创建应用，`uvicorn api:app` 仍通过惰性属性兼容。

## 验证与回滚

```powershell
python tools/check_architecture.py
python -m pytest tests/ -q
python start.py --help
python start_web.py --help
```

架构检查已加入后端 CI，与 pytest 同一 job 执行。它检查层序、公共层依赖、工具反向依赖、导出预算、薄门面、编排类大小、旧路径和路由模块大小。HTTP 路径、方法和注册顺序固化在 `tests/fixtures/api_routes.yaml`。

每个重构阶段都是独立提交，且在全量测试通过后提交。回滚对应阶段使用 `git revert <commit>`；若回滚多个相互依赖的阶段，按从新到旧的顺序执行。数据库 schema、HTTP 合约、CLI 参数与配置字段均保持原样。
