# 更新日志 / Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)；`version.json` 是应用版本的唯一机器可读来源。

This project follows [Semantic Versioning](https://semver.org/). `version.json` is the single machine-readable source of the application version.

## Unreleased

- 新增轻量 `markitdown` 本地文档归一化层：以单一 `MarkItDown` 入口统一调度 PDF、Office、文本、表格与结构化数据 Converter。
- `provider.tools.document_tools` 现在是兼容适配层，保留历史函数签名、绝对路径校验、稳定 Markdown 映射和原子写入。
- 新增 `python -m markitdown`、`python convert.py` 与 Windows `convert.cmd` 转换入口；网络链接、视频、音频、OCR 和云服务明确排除。

## 1.3.0 — 2026-08-22

### Added / Changed

- Graph extraction now defaults to the coarse `large` profile, with configurable `small` / `medium` / `large` section sizes and hard entity/relation budgets.
- Coarse structured extraction filters weak and over-dense relations; tool extraction returns recoverable budget/evidence errors so the LLM can continue and finish the document transaction.
- Retrieval keeps the original query, adds bounded planner variants, and supplements FAISS with exact lexical candidates for short terms and identifiers.
- Search-cache format bumped to `3` so old results cannot mask the revised retrieval semantics.
- `KnowledgeBaseService` now exposes document, graph, retrieval and maintenance domain services behind the original compatible facade; HTTP, CLI and Web callers keep their public contracts.
- Same-version update is now available consistently from `update.py`, CLI, Web and local HTTP API, with optional `force` propagation and safe rollback after post-merge dependency/build failures.
- Tool-call loops now default to serial Kemo requests and retry one transient gateway/provider failure with an incremented protocol attempt, preserving provider error metadata for diagnostics.

### Documentation / Compatibility

- Updated the bilingual README and external API guide for graph granularity, retrieval behavior, service boundaries and forced-update parameters.
- Existing public method signatures, HTTP routes and CLI output envelopes remain compatible. Automatic updates still require a clean Git source checkout; deployment-package replacement is not included.

## 1.2.1 — 2026-08-06

### 新增 / Added

- 新增 `POST /api/v1/stores/import` multipart 文件上传导入端点（配合 kemo-agent `import_file` 命令跨文件系统投递）：`UploadFile` + `Form(store_root)`，先做格式白名单与 50MB 大小校验，再流式暂存到短生命周期临时目录后走既有导入链路；`store_root` 用表单字段传递，避免路径进入 URL 日志。
- 内置库侧同步配套 `POST /api/v1/import` 的 multipart 上传能力，kemo-agent `import_file` 按库类型自动选择端点。
- 契约测试新增 multipart 上传用例（`?ingest=false`、返回双哈希），API 文档补充「multipart 文件上传导入」小节。

### 修复 / Fixed

- RST 转换使用 `publish_parts(source=rst, writer_name="html5")`，与当前支持的 docutils 版本兼容。

### 兼容性 / Compatibility

- 现有 HTTP 端点、CLI 命令和默认项目知识库继续兼容；`/stores/import` 为新增端点。
- 前端 `package.json` 的版本仅代表私有构建包，不作为应用发布版本。

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
