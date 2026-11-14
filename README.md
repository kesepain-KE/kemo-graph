# kemo-graph

<p align="center">
  <img src="kemo-graph-logo.png" alt="kemo-graph logo" width="200">
</p>

<p align="center">
  <strong>简体中文</strong> · <a href="README.en.md">English</a>
</p>

<p align="center">
  <strong>面向 Kemo 生态的本地图谱与检索基础设施。</strong>
</p>

<p align="center">
  将多格式资料沉淀为可追溯的知识图谱与向量索引，<br>
  让智能体不仅能找到原文，也能理解概念、关系、来源与上下文。
</p>

<p align="center">
  <a href="version.yaml"><img src="https://img.shields.io/badge/version-2.0.0-00a98f" alt="version 2.0.0"></a>
  <a href="https://github.com/kesepain-KE/kemo-graph"><img src="https://img.shields.io/badge/status-early%20development-5966d9" alt="status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license"></a>
  <a href="api.md"><img src="https://img.shields.io/badge/API-agent%20integration-0ea5e9" alt="API"></a>
</p>

---

## 如果智能体不必每次都从文件堆里猜答案

一个长期使用的智能体，终究会遇到同样的问题。

资料越来越多：项目文档、课程笔记、方案草稿、网页导出、PDF、Word、表格与零散记录都被妥善保存下来。可当智能体真正需要回答一个问题时，它往往只能临时检索几段相似文字，再从中推测上下文。

它或许找得到“出现过什么”，却不一定知道：这个概念和哪些概念有关；一条关系由哪些资料共同支持；某段检索结果来自哪份原文；文件更新或删除后，哪些知识仍然可信。

**kemo-graph 想成为 Kemo 生态中专门处理这件事的一层基础设施。**

它把原始资料、转换后的 Markdown、图谱节点、关系证据与文本向量连在同一条可维护的链路上。于是，智能体不只是在文件里“搜索答案”，而是可以沿着知识结构找到关系，再回到原文确认依据。

它不是另一位负责聊天的智能体，而是让智能体能够长期使用资料、理解资料并持续维护资料的知识层。

---

## 它能做什么

| 场景 | 能力 |
|---|---|
| 项目知识沉淀 | 把设计文档、笔记、决策记录逐步连接为可查询的概念网络 |
| 智能体知识协作 | 让 kemo-agent 在需要时通过 API 取得图谱关系与原文证据 |
| 多格式资料整理 | 本地 PDF、Word、PowerPoint、Excel、邮件、文本、表格与结构化数据统一转为 Markdown |
| 来源追溯 | 从图谱或检索命中回到对应原文，避免只有结论没有依据 |
| 多路检索 | 图谱、向量、混合、问答与全局主题五种方式，适合不同问题 |
| 可调抽取与稳健召回 | 图谱抽取支持细/标准/粗颗粒度；检索结合查询扩展、FAISS 多路融合与精确词面兜底，兼顾语义与短词命中 |
| 增量维护 | 文件变化后只更新受影响的数据，而不是反复重建整个知识库 |
| 原文件知识库 | 三栏浏览实际文件目录，支持嵌套文件夹、原文件预览、文本编辑与移动；处理产物单独保存并映射回来源 |
| 过程可见 | 独立构建状态页、真实检索步骤进度，以及终端／查询／内部运行日志 |
| 低开销读取 | 通过有界内存缓存复用未变化的状态、日志与知识库指纹，减轻重复磁盘读取 |
| 安全删除 | 删除文档或节点时检查共享来源，尽量避免误伤其他资料支撑的知识 |
| 独立部署 | 提供本地 Web、CLI 与 HTTP API，也可接入更大的 Kemo 智能体系统 |

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（构建网页前端时需要）
- Git
- 可访问的 Kemo 网关（kemo-adapter-api），并已注册可用的 LLM、Embedding 与 Rerank 模型

### 获取并启动

以下为 Windows PowerShell 示例：

```powershell
git clone https://github.com/kesepain-KE/kemo-graph.git
cd kemo-graph

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item config\config.example.yaml config\config.yaml
```

在 `config/config.yaml` 中至少配置网关调用密钥：

```yaml
kemo:
  api_key: "你的-kemo-网关调用密钥"
```

应用配置统一使用 UTF-8 YAML：`config/config.yaml` 保存密钥、模型、存储路径与 `web.host` / `web.port`，该文件不进入 Git；`config/config.example.yaml` 提供无密钥模板，`config/logging.yaml` 使用标准 Python logging 配置，`version.yaml` 保存发布版本。旧 JSON 配置和 `.env` 不再加载；部署环境仍可通过进程环境变量覆盖路径、监听地址或提供网关密钥。npm、TypeScript 和 Python 构建工具的原生清单保留其要求的 JSON/TOML 格式。

图谱抽取默认采用 `large` 粗粒度，可在网页“系统配置”或 `config/config.yaml` 中调整为 `small`（细）、`medium`（标准）或 `large`（粗）；同时可用 `graph_extract_chunk_size` 调整基准分段大小。粗粒度会限制每段实体和关系预算，细节优先写入节点摘要，避免图谱膨胀。检索会自动保留原始问题，并在安全范围内扩展同义词、批量召回，再用精确词面通道补救中文短词和标识符命中。

修改图谱档位后，下一次扫描会自动将已完成文档标记为 Graph 待重建；可运行 `python start.py rebuild-knowledge-base` 重新整理，而无需因此重建未变化的 RAG 向量。

在 `config/config.yaml` 中确认网关地址与所需模型，然后构建网页前端并启动：

```powershell
cd web\frontend
npm install
npm run build
cd ..\..

python start_web.py
```

默认访问 `http://127.0.0.1:8000`。

Linux/macOS 终端可使用同一启动入口：

```bash
git clone https://github.com/kesepain-KE/kemo-graph.git
cd kemo-graph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
# 编辑 config/config.yaml，配置网关、模型、路径与监听地址
cd web/frontend
npm ci
npm run build
cd ../..
python start_web.py
```

---

## 基本使用

### 网页

启动后即可在浏览器中：按实际目录管理原文件、预览多格式资料与编辑文本、查看独立构建状态、浏览知识图谱、使用五种检索模式，并追踪后台任务、检索步骤与运行日志。

<p align="center">
  <img src="kemo-graph-web.png" alt="kemo-graph 网页端知识检索界面" width="100%">
</p>

<p align="center">
  <sub>本地优先的知识检索工作台：统一使用图谱、向量、混合检索与 LLM 回答。</sub>
</p>

#### 原文件目录与处理映射

知识库由三栏组成：左侧为原文件根目录树，中间为当前目录的第一层内容与相对路径，右侧显示文件名、大小、路径及完整内容。拖动两条分隔线可调整栏宽；页面顶部保留名称/路径搜索与刷新，目录栏的 `+` 菜单提供新建文件夹、新建文档与导入。目录可任意嵌套，无内置共享、用户或项目分组。

- 原文件保存在 `external/originals/`；规范 Markdown 保存在 `external/markdown/`。自定义 Markdown 路径时，原文件目录位于其同级 `originals/`。
- `sources.library_path` 和 `file_map.json` 将原文件路径映射到独立的 Markdown 产物。Graph/RAG 读取处理产物，检索与图谱来源链接定位原文件。
- 文件菜单提供重命名、移动、构建与删除。改名或移动保留 `source_id`、处理产物路径和已有索引，同时更新来源映射；不调用模型。同目录同名不同内容的导入会返回冲突。
- 刷新会发现磁盘新增、修改与删除，重新生成变化文件的处理产物；编辑与刷新本身不调用模型，随后可构建指定文件或目录。
- 删除会永久移除原文件、处理产物及对应 Graph/RAG 数据。后台构建期间请等待完成再移动资料。
- Portable Store 使用 `content/originals/` 与 `content/markdown/`，通过 `/api/v1/stores/library/{list,create,move,delete,refresh}` 管理实际目录，`store_root` 在请求体中指定。

旧版仅保留处理后 Markdown 的资料不会被伪装成原文件。升级后请重新导入原始资料以获得原格式预览；旧索引与处理产物保留在磁盘。旧项目组织 API 已移除，调用方需改用目录 API。

#### 检索进度

知识检索以短标签显示本次实际执行的步骤，包括 LLM 优化、关键词与问题拆分、查询向量化、图谱／向量／关键词召回、切片聚合、重排序及回答生成。未执行的步骤不显示为完成；缓存命中和规划回退有独立标记。切换站内页面不会中断检索，刷新或关闭网页不保留等待状态。

文档切片与文档向量化发生在建库阶段；检索阶段处理的是问题与已有切片，不会为了展示进度而重新切分、重建文档。实时步骤使用进程内短期状态，不新增模型调用或磁盘进度日志；多进程部署需将查询与进度轮询路由到同一工作进程，否则只显示该进程可确认的步骤。

#### 构建状态

“构建状态”采用顶部概览、左侧文档队列、右侧处理稿的布局。顶部并排显示 RAG 与知识图谱的四种状态数量，点击数量可组合筛选；右侧仅保留重新构建失败项、搜索与刷新。文档卡片同时显示两条链路，展开后可查看真实成功步骤、失败位置和错误，定位原文件或重新构建单个文件。旧文档没有步骤记录时如实显示暂无记录。

右侧直接预览独立处理目录中的 Markdown，显示文件大小、原文件位置和处理稿路径，并支持编辑。“确认保存，不构建”只保存处理稿；“保存并重新构建”保存后重建该文件的两条链路。编辑不改原实体文件；旧索引保留至重建替换。批量失败重建仅重试失败链路，不带入其他待处理文档。每页最多显示 6 篇文档，列表和预览各自滚动；支持手动或每 5 秒刷新，离开页面不影响后台构建。

#### 运行状态与日志

“运行状态”页提供独立的运行日志卡片，可切换**终端启动日志、查询日志、内部运行日志**，按 UTC 日期查看；支持手动刷新、每 5 秒自动刷新与跟随最新记录。日志在卡片内部滚动，不撑长整页。

日志文件和终端输出均采用 TSV 表格，固定六列：`time`、`level`、`module`、`action`、`detail`、`elapsed_ms`。网页同样显示六列，表头固定，长详情自动换行，窄屏在表格内部横向滚动。重启辅助日志保存为 `update/runtime/restart.tsv`。

终端标签展示启用记录后的 Web 启停及 Python/Uvicorn 日志，不回补历史 shell 输出，也不捕获未接入 logging 的第三方直接输出。查询日志包含开始、完成、失败、耗时、缓存和召回事件，使用 `query_id` 关联查询生命周期，不额外记录问题正文或模型回答。默认显示最近 200 条，单次只扫描当天日志文件末尾 2 MB；密钥、Bearer、Token 等敏感内容会脱敏。升级后需重启服务才能开始记录新增终端日志。

#### 内存读取缓存

长驻 Web/API 进程共享一层有界内存读取缓存，用于知识库指纹、文档分页、运行状态（含 FAISS 健康检查结果）、日志、配置正文和查询规划提示词摘要，减少未变化数据的反复读取。它不替代原有 SQLite 搜索结果缓存，也不会改变强制查询、清理搜索历史或数据库写入的行为。

- 全进程最多 256 项、32 MiB 的序列化缓存数据，单项最多 2 MiB；这是缓存载荷限制，不是整个进程的内存限制。超限按最近使用情况淘汰，大项直接读取而不保留。
- 每次读取先检查文件身份、大小和修改信息；SQLite 额外检查 100 字节文件头。正常提交、改名、删除及数据库替换会使对应读取失效；未变化条目也最多复用 30 秒。
- 非空 WAL 或事务日志存在时保守绕过数据库读取缓存，避免只看主数据库而漏掉尚未回写的变化。不持有长期数据库连接，兼容项目重建替换文件。
- 相同数据的并发读取合并执行，返回内容相互隔离；失败结果不进入缓存。日志缓存同时区分当前脱敏密钥，密钥变化后重新脱敏。
- 缓存按绝对 Store 路径隔离，重启后为空；多个服务进程各有自己的内存缓存，CLI 单次进程仅在本次调用期间受益。数据库与日志仍正常落盘，不使用延迟写入。

#### 知识文档预览与渲染

网页直接读取原文件：Word、PDF 和 PowerPoint 使用 Apache-2.0 开源项目 File Viewer 的 React 组件及对应渲染模块，PDF 引擎为 Mozilla PDF.js；支持分页、缩放、搜索及原件下载。官方 Vite 插件在构建时将配套 Worker、WASM 和字体资源放入 `dist/assets/file-viewer`，随网页一起部署，不依赖设备安装桌面 Office、LibreOffice 或公共 CDN，也不向第三方预览服务上传文档。Word 和 PowerPoint 保留文档与幻灯片版式；特殊字体、动画及复杂 Office 特性仍以桌面应用显示为准。

图片直接显示，XLS/XLSX/XLSM 展示所有工作表，另支持 EPUB、RTF、EML 与 HTML。二进制文件可下载后本地编辑。HTML 在受限沙箱中显示。原文件预览与图谱使用的 Markdown 转换独立；展开预览约占屏幕宽度的 84%、高度的 90%，点击外部遮罩可关闭。

Markdown 预览读取原始 `.md` / `.markdown` 文件，使用 `react-markdown`、GFM、KaTeX 和按需加载的 Mermaid 渲染器，支持：

- 标题、粗体、斜体、删除线、嵌套列表、任务列表、引用块、水平分隔线、表格、脚注、链接、图片和代码块；
- 行内数学公式 `$a^2+b^2=c^2$` 与行间公式 `$$...$$`；
- 常见 LaTeX 外层分隔符 `\(...\)`、`\[...\]`，以及 `aligned`、`cases`、矩阵、分数、积分、求和、极限、中文 `\text{}` 和 `\xrightarrow{}`；
- 代码语言高亮；
- Mermaid fenced block，例如：

  ````markdown
  ```mermaid
  flowchart TD
      A[原始资料] --> B[Markdown]
      B --> C[知识图谱]
      B --> D[RAG 向量]
  ```
  ````

- Obsidian 风格双链：`[[数据结构]]`、`[[数据结构|查看结构]]`；
- Obsidian 风格 Callout：

  ```markdown
  > [!NOTE] 说明
  > 这是会被渲染成主题化信息卡片的正文。
  ```

- `![[文档或图片]]` 嵌入语法的安全占位显示；
- YAML/TOML frontmatter 识别，并从正文阅读区隐藏。

公式渲染采用 KaTeX。复杂公式不会撑破预览气泡，超宽矩阵和长公式会在文档内部横向滚动。Mermaid 只在文档实际出现图表时动态加载，普通文档不会承担图表引擎的初始化开销。

为避免导入文档成为脚本注入入口，预览默认不执行原始 HTML 和 `<script>`；网页、视频、音频、OCR、远程链接等内容仍应由上游智能体先处理成 Markdown 或本地文件。KaTeX 不支持的自定义宏会保留原始公式并显示局部错误提示，不会导致整篇文档白屏。

### 命令行

```powershell
# 只转换为 Markdown，不初始化知识库，也不调用模型
python -m markitdown $env:KEMO_GRAPH_IMPORT_FILE -o output.md
python convert.py $env:KEMO_GRAPH_IMPORT_FILE -o output.md

# 导入文件（先不消耗模型额度）
python start.py import $env:KEMO_GRAPH_IMPORT_FILE --no-ingest

# 扫描并整理所有待处理文档
python start.py ingest

# 查询
python start.py query-hybrid "知识图谱如何增强检索"
python start.py query-answer "请综合图谱和原文回答"

# 将 kemo-agent 等上游表记录同步到其独立 Store
python start.py --store-root $env:KEMO_GRAPH_STORE_ROOT source-sync records.json

# 状态与维护
python start.py status
python start.py list-docs
python start.py organize-graph
python start.py rebuild-all

# 检查并应用更新
python start.py update-check
python start.py update
python start.py update-changes

# 根目录更新入口：同版本时会询问是否强制重新执行更新
python update.py

# 更新前先看有哪些未提交的更改，或备份未提交修改后强制更新
python update.py --dirty
python update.py --force
```

文档归一化层只处理本地普通文件。网页抓取、视频、音频、OCR 与远程链接应由上游智能体先处理，再把 Markdown 或本地文件交给 kemo-graph；转换器不会访问网络。

### HTTP API

kemo-graph 可作为独立服务运行，向 kemo-agent 等智能体提供图谱查询、混合检索、导入与维护等端点：

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

完整请求字段、响应包络与错误码约定见 [api.md](api.md)。

---

## 数据与隐私

- 原文件、转换后的 Markdown、图谱与索引都保存在本地工作目录；本地资料以原文件为来源，Graph/RAG 使用独立的处理产物，外部同步记录仍以上游系统为权威来源。
- 图谱构建、Embedding 与 Rerank 会通过 Kemo 网关调用模型；处理敏感资料前，请确认网关、模型与网络边界。
- 应用查询生命周期日志不额外保存问题正文或模型回答，日志查看会脱敏已知密钥与常见认证字段；第三方 Python 日志仍可能包含业务信息，请保护日志和服务访问权限。

> **注意**：当前外部 API 没有内建应用层鉴权。默认应监听 `127.0.0.1`；若需跨设备或公网访问，请在外部部署 VPN、反向代理、TLS 或认证层，不要直接暴露未受保护的端口。

---

## 我们希望它成为什么

kemo-graph 不试图成为替代所有文件管理、所有数据库或所有搜索系统的项目。

它更希望成为 Kemo 生态中稳定的一层知识基础：资料进入系统后仍然保留来源与可追溯性；关系可以被发现，但不会脱离原文证据；文件发生变化时，系统知道该更新什么、保留什么；模型与 Provider 可以变化，但本地事实来源始终留在用户手中。

真正能陪伴长期项目的智能体，不应只拥有更长的上下文窗口，也应拥有一套能够持续理解资料、验证来源并维护结构的知识基础。

---

## 当前状态

核心闭环已经可以实际运行：统一导入、增量更新、图谱与向量检索、混合问答、安全删除、定时维护，以及本地 Web、CLI、HTTP API 三个入口和面向 kemo-agent 等智能体的外部知识服务接口。

当前版本为 **2.0.0**。知识库按真实文件目录管理原始资料，通过独立映射维护处理稿；Graph/RAG 构建提供后台任务、逐文档进度、失败重试及处理稿编辑。图谱与全文检索使用统一的分词和检索能力，网页工作区同步更新。转换层统一异常语义和格式能力声明，并支持按内容识别无扩展名文件。原文件预览与索引转换保持独立，处理产物继续过滤图片二进制、Data URI、脚本和样式。

PDF 中可识别的网格表格会保留为 Markdown 表格，表旁及表后正文不丢弃，无表格页面保持原有输出。新增 DOCM、PPTM 内容读取，延续 XLSM 支持，不执行 VBA；DOCM 必须使用 Mammoth 与 markdownify，不回退到 python-docx。完整格式边界、依赖及限制见 [转换层说明](markitdown/README.md)。

完整更新摘要、升级注意事项与发布验证命令见 [CHANGELOG.md](CHANGELOG.md)。应用版本以根目录 `version.yaml` 为准，前端包和转换层包的发布元数据同步为同一版本；本版本已完成 Kemo 2.0 Provider 适配，继续使用 `/api/v1` 的项目 HTTP API 路径。

仍在持续打磨：复杂文档版式的转换质量、大知识库与高并发下的存储与索引策略、外部 API 的内建鉴权与权限分层、更丰富的图谱人工校正与来源审查界面。

如果你正在试用早期版本，欢迎提交遇到的问题、检索体验、资料格式样本，以及你希望智能体如何使用知识库的真实场景。

---

## Kemo 生态相关项目

- [kemo-agent](https://github.com/kesepain-KE/kemo-agent) — 面向个人智能基础设施的本地多用户 Agent Runtime，可通过 kemo-graph API 使用图谱与 RAG 知识能力。
- [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api) — Kemo 统一模型网关，以统一协议向生态组件提供 LLM、Embedding、Rerank 模型能力。

它们可以分别独立使用，也可以在同一套本地智能基础设施中各司其职。

---

## 主要维护者

[@kesepain](https://github.com/kesepain-KE)

---

## 参与贡献

kemo-graph 仍处于早期开发阶段。无论是问题报告、格式转换样本、检索质量反馈、文档改进还是功能贡献，都很欢迎。

推荐流程：Fork 本仓库 → 创建功能分支 → 完成修改并运行必要测试 → 提交 Pull Request，说明改变了什么、为什么这样改，以及验证结果。

---

### 后端开发入口

统一知识库服务使用 `from core import KnowledgeBaseService`，也可从 `core.service` 导入。配置与存储分别位于 `core.settings`、`core.storage`，图谱、RAG、构建和维护分别位于 `core.graph`、`core.rag`、`core.ingest`、`core.maintenance`；旧的扁平模块兼容路径已移除。API 路由与模型分别位于 `api/routes/`、`api/schemas/`，HTTP 接口保持不变。

开发前运行 `python tools/check_architecture.py` 和 `python -m pytest tests/ -q`。模块层序与导出规范见 [核心服务说明](core/service/README.md)。

---

## 开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。
