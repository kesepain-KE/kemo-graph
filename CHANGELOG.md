# 更新记录 / Changelog

## 1.4.0 — 2026-09-13

### 本次更新

- 文档管理支持项目文件夹、指定项目上传、重命名、单篇与批量移动；移动和改名保留来源 ID、正文哈希及现有索引。
- 搜索框上移，Graph / RAG 状态筛选迁入独立“构建状态”页面；每页最多 6 篇文档，支持内部滚动与自动刷新。
- 检索进度保留简短文字，并显示实际发生的规划、查询向量化、召回、切片聚合、重排序和回答生成；支持缓存命中与 LLM 回退状态。
- 运行状态新增终端启动、查询和内部运行日志标签，支持日期选择、刷新、内部滚动与脱敏。
- 新增有界内存读取缓存，减少未变化文档状态、日志、配置及知识库指纹的重复磁盘读取；回收用完的搜索并发锁。
- 精简系统维护页面，更新区域仅保留重启服务；同路径回收站副本允许覆盖，并处理失败回滚。
- 保留原有本地文件导入核验、外部 Store 隔离、Markdown 渲染和检索接口。

### 升级说明

- 应用版本统一为 1.4.0：`version.json`、前端包及锁文件、Python 转换层包元数据和说明文档同步。Kemo 1.0、`/api/v1` 与存储格式版本不随应用版本改号。
- 更新源码并安装所需依赖、构建前端后重启服务；不必仅为版本升级清空或重建现有知识库。正文或抽取设置发生变化时仍按原流程手动重建。
- 新增终端日志无法补录过去未保存的输出。内存读取缓存与检索步骤记录会在进程重启后清空。
- 检索步骤记录为进程内状态，多工作进程需要同进程路由；没有步骤回报时不伪造阶段完成情况。
- 本版本没有新增应用层 API 鉴权，网络部署仍须保护访问边界。

### Release summary

Project-based document organization; dedicated Graph/RAG build status; concise, real query-stage telemetry; categorized runtime logs; bounded read-through memory caching; simpler maintenance controls; and same-path recycle replacement with rollback handling. Existing retrieval response formats and external Store isolation remain compatible.

The application, frontend and converter-package metadata share version 1.4.0. Protocol and storage-format versions are independent. Rebuild the frontend and restart the service after updating; no blanket knowledge-base rebuild is required merely because the application version changed.

### 发布验证 / Release verification

```bash
python -m pytest tests/ -q
python -m compileall -q core api provider markitdown update start.py start_web.py
python start.py version
cd web/frontend
npm run typecheck
npm test -- --run --pool=threads --maxWorkers=1 --minWorkers=1
npm run build
```

单工作线程测试是为了降低本地测试进程开销，不改变生产环境的运行方式。真实模型质量、GPU 硬件表现及操作系统差异仍需在目标部署环境验收。
