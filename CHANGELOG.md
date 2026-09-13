# 更新记录 / Changelog

## 1.5.0 — 2026-09-13

### 本次更新

- 更新入口新增只读检查：`python update.py --dirty`（别名 `--changes`）与 `start.py update-changes` 列出工作区中未提交的更改，并区分会阻塞更新的程序文件，以及不影响更新的用户配置与运行数据。
- 更新入口新增强制选项：`python update.py --force` 在存在未提交的程序文件修改时，先备份到 `update/runtime/dirty-backup/<时间戳>/` 并还原工作区，再继续安装；安装失败会把这些修改放回原位置。
- 更新被拒绝时，错误对象附带 `hint` 字段，直接给出查看未提交更改与强制更新的命令。
- 重启服务改用无控制台的窗口版解释器：Windows 上 `restart.py` 启动替换进程时，会把命令中的 `python.exe` 换成同目录的 `pythonw.exe`。此前服务进程若因任何路径持有控制台，重启后会带出一个终端窗口，而那个窗口本身就是服务进程——关掉窗口等于停掉服务；使用不持有控制台的解释器后，重启后的服务与终端彻底解耦。`restart.log` 仍完整记录替换进程的输出。
- 编辑器与手动备份文件（`*.bak`、`*.bak.*`、`*.orig`）不再被计为未提交的程序文件修改。
- 修复 `restart_required` 无法自动清除：该标志在更新成功后置位，但此前没有任何代码会复位它，服务即便已经重启，状态页仍会一直提示需要重启。服务启动本身就是该标志所要求的那次重启，因此现在在启动时终结它；没有更新记录时不会为此新建状态文件。
- Web 系统配置页与 `/api/v1/update/*` 接口的既有行为保持不变。

### 升级说明

- 应用版本统一为 1.5.0：`version.json`、前端包及锁文件、Python 转换层包元数据和说明文档同步。Kemo 1.0、`/api/v1` 与存储格式版本不随应用版本改号。
- 旧版更新入口在检测到未提交的程序文件修改时仍会拒绝更新；需要先更新到 1.5.0 才能使用 `--dirty` 与 `--force`。
- 未提交修改的备份保存在 `update/runtime/dirty-backup/`，属于运行状态目录，不会被更新覆盖，也不会进入版本库。
- 前端依赖统一使用 npm：移除仓库中的 `pnpm-lock.yaml` 与 `pnpm-workspace.yaml`。更新器、CI 与文档一直使用 `npm ci` 与 `npm run build`，保留两份锁文件只会让它们各自演进；此前 `pnpm-workspace.yaml` 中的构建脚本审批占位符还会在 pnpm 安装时被反复改写，进而把工作区标记为未提交。
- `--force` 会临时移除工作区中的未提交修改；需要对照原始内容时保留该备份目录即可。

### Release summary

Read-only worktree inspection before updating; an explicit `--force` that backs up uncommitted program changes and restores them when the update fails; actionable refusal hints; and editor backup files no longer counted as uncommitted program changes.

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

### 持续集成 / Continuous integration

- 仓库新增 GitHub Actions 工作流 `CI`，在推送与合并请求时自动执行发布前检查：后端在 Python 3.10 与 3.12 上安装 `requirements-dev.txt`、运行语法检查与全部单元测试；前端执行类型检查、单元测试与生产构建。
- 这些检查此前只在发布前手工执行，现在由 CI 固定执行，不必依赖本地环境是否完整。
- 同时补充两类行为测试：失败来源重试开关（`retry_failed`）的默认保守行为与 API 透传，以及知识库的符号链接边界防护。符号链接用例在无法创建链接的平台自动跳过，由 Linux 运行器覆盖。
- 工作流首次运行即暴露两处只在本机成立、新克隆会复现的缺陷，均已修复：忽略规则中的 `data/` 曾把 `web/frontend/src/features/graph/data/` 源码目录一并排除在仓库之外，导致新克隆无法通过前端类型检查；Web 路由测试隐式依赖本地已执行过 `npm run build` 的前端产物。

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
