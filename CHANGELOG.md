# 更新日志 / Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)；`version.json` 是应用版本的唯一机器可读来源。

This project follows [Semantic Versioning](https://semver.org/). `version.json` is the single machine-readable source of the application version.

## 1.2.0 — 2026-08-05

### 新增 / Added

- 新增外部权威来源同步协议，支持通过稳定 `source_uri` 同步、分页查看和删除记忆等权威表记录。
- 新增 `/api/v1/stores/sources/sync`、`/status`、`/delete` Store API，以及对应的 `source-sync`、`source-status`、`source-delete` CLI 命令。
- 文档转换新增 PowerPoint、Excel、EPUB、RTF、TSV、JSON/JSONL、YAML 和 XML，并改进常见中文编码、表格、PDF 与 DOCX 文本提取。
- 新增外部来源身份、修订版本、元数据和内容哈希的数据库迁移与状态查询能力。

### 改进 / Changed

- 网页图谱默认采用 GPU 优先、高性能渲染配置，提升节点标签清晰度并将 SVG 保留为最终兼容回退。
- 图谱与检索结果增加分页和容器内滚动优化，减少大结果集对页面布局的影响。
- 文档管理界面同步扩展可上传格式。
- `Ctrl+C` 停止 Web 服务时进行安静、可识别的正常退出。
- API 文档改用部署无关的路径占位符，并补全外部智能体调用、数据域隔离和来源同步契约。

### 兼容性 / Compatibility

- 现有 HTTP 端点、CLI 命令和默认项目知识库继续兼容。
- `memory.user` 用于每用户统一记忆 Store；旧 memory scope 保留兼容。
- 前端 `package.json` 的版本仅代表私有构建包，不作为应用发布版本。

## 1.1.1 — 2026-08-02

- 优化分层向量检索、父级上下文展开与检索结果展示。
- 完善安全更新入口、中英文 README 与智能体 API 文档。
