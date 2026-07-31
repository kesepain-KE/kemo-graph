# kemo-graph

<p align="center">
  <img src="kemo-graph-logo.png" alt="kemo-graph logo" width="200">
</p>

<p align="center">
  <strong>简体中文</strong>
</p>

<p align="center">
  <strong>面向 Kemo 生态的本地图谱与检索基础设施。</strong>
</p>

<p align="center">
  将多格式资料沉淀为可追溯的知识图谱与向量索引，<br>
  让智能体不仅能找到原文，也能理解概念、关系、来源与上下文。
</p>

<p align="center">
  <a href="https://github.com/kesepain-KE/kemo-graph"><img src="https://img.shields.io/badge/status-early%20development-5966d9" alt="status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license"></a>
  <a href="api.md"><img src="https://img.shields.io/badge/API-agent%20integration-0ea5e9" alt="API"></a>
</p>

---

## 如果智能体不必每次都从文件堆里猜答案

一个长期使用的智能体，终究会遇到同样的问题。

资料越来越多：项目文档、课程笔记、方案草稿、网页导出、PDF、Word、表格与零散记录都被妥善保存下来。可当智能体真正需要回答一个问题时，它往往只能临时检索几段相似文字，再从中推测上下文。

它或许找得到“出现过什么”，却不一定知道：

- 这个概念和哪些概念有关；
- 一条关系由哪些资料共同支持；
- 某段检索结果来自哪份原文；
- 文件更新或删除后，哪些知识仍然可信；
- 多份资料之间，是否已经自然形成了不同的知识群。

**kemo-graph 想成为 Kemo 生态中专门处理这件事的一层基础设施。**

它不把资料看成一次性喂给模型的上下文，也不把图谱当成脱离原文的关系展示。它把原始资料、转换后的 Markdown、图谱节点、关系证据、文本切片与向量索引连在同一条可维护的链路上。

于是，智能体不只是在文件里“搜索答案”，而是可以沿着知识结构找到关系，再回到原文确认依据。

它不是另一位负责聊天的智能体，而是让智能体能够长期使用资料、理解资料并持续维护资料的知识层。

---

## Kemo 生态里的知识层

Kemo 生态希望把一个长期运行的智能系统拆成清晰协作、彼此独立又能互相连接的部分。

```text
                    用户、文件、任务与真实世界
                                │
                                ▼
┌──────────────────────────────────────────────────────────┐
│                       kemo-agent                           │
│  理解用户意图 · 维护长期记忆 · 编排子代理与工具 · 推进任务  │
└───────────────────────┬──────────────────────────────────┘
                        │ Kemo-Graph 外部 API
                        ▼
┌──────────────────────────────────────────────────────────┐
│                       kemo-graph                           │
│  文档导入 · Markdown 化 · 知识图谱 · 向量检索 · 来源追溯    │
└───────────────────────┬──────────────────────────────────┘
                        │ Kemo 协议
                        ▼
┌──────────────────────────────────────────────────────────┐
│                   kemo-adapter-api                         │
│  LLM · Embedding · Rerank 的统一协议、能力声明与模型路由    │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
               已授权的模型 Provider 与本地模型
```

这三者不是简单的“上游”和“下游”关系。

- **kemo-agent** 负责理解：什么时候需要知识、应当查询什么、检索结果如何参与后续判断与行动；
- **kemo-graph** 负责沉淀：资料如何变成可维护的图谱与向量数据，查询如何返回结构与原文证据；
- **kemo-adapter-api** 负责连接：以统一的 Kemo 协议将图谱构建、Embedding、Rerank 请求路由到真实可用的模型能力。

因此，kemo-graph 可以独立作为本地知识库使用，也可以成为 kemo-agent 的外部知识后端。前者适合管理自己的资料；后者让智能体在跨越多次对话、多个任务与多个入口时，仍能访问同一份经过整理的知识结构。

> 记忆让智能体延续对人的理解；图谱让智能体延续对资料的理解。

---

## 资料不该在导入后失去来处

一份文件进入知识库，不应在转换、切片和向量化之后变成无法解释的黑箱。

kemo-graph 的处理链路是：

```text
原始文件
  ↓
转换为可审查的 Markdown
  ↓
source_id + 内容 SHA-256 哈希登记
  ├─ 图谱构建：节点、关系、来源证据
  └─ RAG 构建：文本切片、Embedding、FAISS 索引
  ↓
图谱结果与向量结果始终能回到对应 Markdown
```

它使用内容哈希追踪每份资料的实际状态：

- 文档没有变化时，不重复调用模型，也不重复构建索引；
- 文档新增时，只处理新增内容；
- 文档修改时，只替换该文档参与的节点来源、关系证据和向量切片；
- 文档删除时，只清理该文档对应的数据与索引；
- 多份文档共同支持同一个节点或关系时，各自保留独立的来源与哈希绑定。

这让图谱与向量检索不是一次性的处理结果，而是可以随着资料长期演化的派生知识。

文件系统中的 Markdown 是事实来源；SQLite 图谱、RAG 原始向量与 FAISS 索引都可以据此检查、更新或重建。

---

## 知识不只有“相似”，也应当有关系

向量检索擅长回答“哪些段落和当前问题最相近”，但它并不天然知道这些段落之间的结构。

知识图谱擅长描述“哪些概念彼此关联”，但仅有关系线又无法替代原文证据。

kemo-graph 保留这两种能力各自的边界，再让它们在需要时协作：

| 方式 | 它回答的问题 |
|---|---|
| 图谱检索 | 这个概念和什么有关？关系向前或向后还能延伸多远？它属于哪一组知识？ |
| 向量检索 | 哪些原文片段最接近当前问题？具体资料是怎么说的？ |
| 混合检索 | 先从图谱命中概念和关联切片，再增强这些切片的向量候选并重排序。 |

混合检索不会把节点、关系线与文档片段伪装成同一种结果：

```text
图谱结果 → 提供结构、方向与概念关系
RAG 结果  → 提供原文片段、分数与来源文档
```

调用方可以同时知道“为什么相关”，也知道“依据在哪里”。

---

## 它可以帮 Kemo 生态做什么

| 场景 | kemo-graph 带来的能力 |
|---|---|
| 项目知识沉淀 | 将设计文档、接口说明、决策记录与代码说明逐步连接为可查询的概念网络 |
| Agent 知识协作 | 让 kemo-agent 在需要时调用外部 API，取得图谱关系、原文片段或混合检索结果 |
| 多格式资料整理 | 将 PDF、DOCX、HTML、CSV 等文件统一转为 Markdown 后再进入知识库 |
| 来源追溯 | 从节点、关系或向量命中回到其对应资料，避免只有结论没有依据 |
| 增量维护 | 文件变化后只更新受影响的数据，而不是反复重建整个知识库 |
| 概念关系发现 | 从文档中识别实体、别名、摘要、标签与关系，逐步形成可浏览图谱 |
| 语义检索 | 用本地 FAISS 召回候选文本，再经 Rerank 返回更相关的原文片段 |
| 混合问答 | 让结构化关系增强语义检索，而不丢失原文证据 |
| 安全删除 | 删除文档或节点时检查共享来源，尽量避免误伤其他资料支撑的知识 |
| 定期维护 | 根据资料变动生成节点群总结，并按生命周期清理回收站内容 |
| 独立部署 | 可以独立提供本地 Web、CLI 与 API，也可以接入更大的 Kemo 智能体系统 |

这些能力不是彼此孤立的功能入口。它们共同服务于一个目标：让智能体面对资料时，拥有可检索的原文、可理解的结构与可持续维护的知识基础。

---

## 模型不是被直接绑定，而是通过 Kemo 协议连接

kemo-graph 不直接固化某一家厂商的私有 API 格式。

图谱构建、Embedding 与 Rerank 都通过 **Kemo 协议** 请求 `kemo-adapter-api`：

```text
kemo-graph
  → /model/responses      图谱构建 LLM 与工具调用
  → /model/embeddings     文本向量化
  → /model/rerank         检索结果重排序
  → kemo-adapter-api
  → 已配置 Provider / 本地模型
```

这种分层带来几个直接结果：

- kemo-graph 不需要理解每个模型厂商的鉴权、响应字段和错误格式；
- 模型路由、能力声明、Provider 切换与密钥边界集中在网关处理；
- 图谱工程只关心“当前模型能否完成 LLM、Embedding、Rerank 任务”；
- 当 Kemo 生态接入新的模型或本地推理能力时，知识层无需为每个厂商重新设计一套协议。

当前默认配置示例：

| 能力 | 默认 Kemo 模型 ID |
|---|---|
| 图谱构建 LLM | `deepseek-deepseek-v4-flash` |
| Embedding | `siliconflow-Qwen-Qwen3-VL-Embedding-8B`（4096 维） |
| Rerank | `siliconflow-Qwen-Qwen3-VL-Reranker-8B` |

模型名、网关地址、切片策略、相似度阈值与维护节奏都由 `config/config.json` 控制。密钥不进入项目配置，而是由环境变量提供给 Kemo 网关调用层。

---

## 图谱构建不是一次性的 JSON 幻觉

kemo-graph 不要求模型一次返回一份看似完整、实际上难以纠正的大型 JSON。

它让图谱构建模型在受控工具范围内逐步工作：

```text
读取当前文档与图谱上下文
  ↓
搜索已有概念
  ↓
决定补充已有节点，或新建独立节点
  ↓
建立关系并绑定当前文档的哈希证据
  ↓
完成构建
```

模型可调用的工具包括概念搜索、节点读取、新增与更新节点、建立关系、删除实体以及完成构建等。图谱构建提示词要求模型在新增概念前先搜索已有知识，以减少同义概念被反复建立为多个节点的概率。

更重要的是，单篇文档的图谱构建运行在同一个数据库事务中：

```text
工具调用与写入全部成功 → 提交整篇文档的图谱变更
模型请求、工具调用或校验失败 → 回滚整篇文档的图谱变更
```

这不是为了追求“自动化得更彻底”，而是为了让知识库在模型偶尔出错、网络偶尔中断时，仍然保持可理解、可恢复的状态。

---

## 同一份知识，不止一个入口

你可以从网页导入资料、在命令行中执行本地操作，也可以让 kemo-agent 或其他自动化程序通过 HTTP API 使用知识库。

入口可以改变，但它们使用的是同一份 Markdown、同一套 Graph/RAG 数据和同一条 Kemo 模型链路。

### 在网页里看见资料如何沉淀

启动 Web 后，你可以：

- 上传 PDF、DOCX、Markdown、TXT、HTML、RST、CSV；
- 查看转换后的 Markdown 以及图谱、RAG 的整理状态；
- 在力导向图中浏览概念节点、关系线和节点群；
- 选择图谱、向量或混合方式检索；
- 查看文档数、节点数、关系数、向量数和 FAISS 健康状态；
- 查看或调整本地配置；
- 管理源文档与回收站生命周期。

网页并不是另一套知识库，而是同一份本地知识状态的可视化入口。

### 在命令行里处理本地事务

```powershell
# 导入文件，但先不消耗模型额度
python start.py import "E:\documents\project.pdf" --no-ingest

# 扫描并整理所有待处理文档
python start.py ingest

# 只构建指定 Markdown 的图谱
python start.py ingest --paths "markdown\project-a1b2c3.md" --mode graph

# 图谱、RAG、混合查询
python start.py query-graph "知识图谱如何增强检索"
python start.py query-rag "如何导入 PDF" --top-k 10
python start.py query-hybrid "图谱和向量检索的关系"

# 查看状态与资料列表
python start.py status
python start.py list-docs
```

CLI 输出结构化 JSON，适合 PowerShell 脚本、定时任务或本地自动化接续处理。

### 让智能体通过 API 使用知识

kemo-graph 可以独立作为 FastAPI 服务运行：

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

智能体可以通过：

```text
/api/v1/query/graph
/api/v1/query/rag
/api/v1/query/hybrid
/api/v1/import
/api/v1/ingest
/api/v1/documents
/api/v1/graph
```

导入资料、触发整理、读取图谱、获取原文片段并执行混合检索。

这正是 kemo-agent 与 kemo-graph 的协作边界：

```text
kemo-agent 决定：现在需要什么知识、问题应如何表达、结果如何继续用于任务
kemo-graph 决定：资料如何维护、关系如何查询、原文如何检索与返回
```

完整的请求字段、响应包络、错误码、删除规则和运行边界，请阅读：

> [kemo-graph 外部智能体 API（api.md）](api.md)

---

## 知识可以增长，也应当可以被谨慎清理

长期资料库不只需要导入能力，也需要明确的删除边界。

### 删除源文档

删除文档时，系统不会只从列表中隐藏它。

```text
Markdown 移入 external/recycle/
  ↓
移除该文档绑定的节点来源与关系证据
  ↓
移除对应文本切片、Embedding、chunk_nodes 与 FAISS 向量
  ↓
重算仍被其他资料支持的节点与关系
```

若节点或关系仍有其他文档来源，它们会继续保留。回收站由 `recycle_life_days` 控制，到期后才会进行永久清理。

### 删除节点

删除一个节点时，系统会先检查与它绑定的每份文档：

```text
文档仍绑定其他节点
  → 仅解除当前节点绑定，文档和向量保留

文档只绑定当前节点
  → 文档进入回收站，并级联清理 Graph / RAG / FAISS 数据
```

这样，修正一个错误概念不会轻易破坏仍被其他知识使用的资料。

当前单条关系线删除能力仍在内部工具层，尚未公开为 HTTP API、CLI 或 Web 操作。它是后续会继续补齐的边界，而不是被隐藏的行为。

---

## 属于你的资料，也应当由你掌管

kemo-graph 坚持本地优先。

转换后的 Markdown、文档映射、SQLite 数据库、FAISS 索引、回收站和运行日志都保存在你的工作目录中：

```text
external/markdown/          转换后的正式 Markdown 与 file_map.json
external/recycle/           已删除、等待生命周期清理的 Markdown
data/sources.db             源文档身份、内容哈希与处理状态
data/Graph/graph.db         节点、关系、来源证据与节点群
data/RAG/rag.db             文本切片、原始向量与切片-节点关联
data/RAG/vector_index/      可由 rag.db 重建的 FAISS 索引
log/YYYY-MM-DD.tsv          按 UTC 日期滚动的运行日志
```

本地保存不代表模型调用天然私密。

当图谱构建、Embedding 或 Rerank 发生时，相关内容会发送给你配置的 Kemo 网关，再由网关路由至已授权的 Provider 或本地模型。是否适合处理敏感资料，取决于你实际选择的网关、模型服务和部署边界。

kemo-graph 所做的是尽可能清楚地保存本地事实来源、来源绑定和操作日志，并让模型连接保持在 Kemo 生态可管理的统一协议边界中。

日志只保留操作摘要、模型名、状态、耗时与错误类别；不会记录 API Key、Bearer Token、完整文档正文或完整模型提示词。

---

## 开始体验

### 环境要求

- Python 3.10+
- Node.js 18+（构建网页前端时需要）
- Git
- 可访问的 Kemo 网关
- 网关中已注册可用的 LLM、Embedding 与 Rerank 模型

### 获取并部署

```powershell
git clone https://github.com/kesepain-KE/kemo-graph.git
cd kemo-graph

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

至少配置 Kemo 网关调用密钥：

```dotenv
KEMO_API_KEY=你的-kemo-网关调用密钥
```

然后检查 `config/config.json`：

```text
kemo.base_url                 Kemo 网关地址
models.llm                    可用于图谱构建的 LLM
models.embedding              Embedding 模型
models.embedding_dimensions   对应向量维度
models.rerank                 Rerank 模型
```

构建网页前端：

```powershell
cd web\frontend
npm install
npm run build
cd ..\..
```

启动网页端：

```powershell
python start_web.py
```

默认访问：

```text
http://127.0.0.1:8000
```

如果更习惯本地操作，也可以直接使用：

```powershell
python start.py status
python start.py import "E:\documents\first-note.md" --no-ingest
```

初次使用建议先导入一份小型 Markdown 或 TXT，先检查转换后的内容，再决定是否开始图谱和 RAG 整理。

---

## 部署与访问边界

服务监听地址可以通过环境变量改变：

```powershell
$env:KEMO_GRAPH_WEB_HOST = "0.0.0.0"
$env:KEMO_GRAPH_WEB_PORT = "8000"
python start_web.py
```

这样可以让其他设备访问服务，但需要注意：**当前 kemo-graph 外部 API 没有内建应用层鉴权。**

若要跨设备、通过不可信局域网或暴露到公网访问，应在 kemo-graph 之外部署 VPN、反向代理、TLS、IP 白名单或认证层。不要直接将未受保护的 `8000` 端口暴露到互联网，因为导入、删除、配置和维护端点都可能修改本地资料或触发模型调用。

---

## 我们希望它成为什么

kemo-graph 不试图成为一个替代所有文件管理、所有数据库或所有搜索系统的项目。

它更希望成为 Kemo 生态中稳定的一层知识基础：

- 资料进入系统后，仍然保留来源与可追溯性；
- 关系可以被发现，但不会脱离原文证据；
- 向量检索可以高效，但不会吞没结构化上下文；
- 文件发生变化时，系统知道该更新什么、保留什么；
- 智能体需要知识时，可以通过清晰 API 获取，而不是读取一堆未整理文件；
- 模型与 Provider 可以变化，但知识库的本地事实来源仍然留在用户手中；
- 能力不断增加，但删除、回收与维护的边界始终清楚可见。

真正能陪伴长期项目的智能体，不应只拥有更长的上下文窗口，也应拥有一套能够持续理解资料、验证来源并维护结构的知识基础。

---

## 当前状态

核心闭环已经可以实际运行：

- PDF、DOCX、Markdown、TXT、HTML、RST、CSV 的统一导入；
- 原始文件与转换 Markdown 的一对一映射；
- 内容哈希驱动的增量图谱与 RAG 更新；
- Kemo 协议下的图谱构建 LLM、Embedding 与 Rerank 调用；
- 工具调用式图谱构建与单文档事务回滚；
- SQLite 图谱、RAG 原始向量与本地 FAISS 索引；
- 图谱、向量、混合三种检索模式；
- Web、CLI、外部 HTTP API 三个入口；
- 文档与节点删除、回收站、节点群总结与定时维护；
- 按日 TSV 运行日志与敏感信息脱敏；
- 面向 kemo-agent 等智能体的外部知识服务接口。

仍在持续打磨的方向：

- 单条关系线删除的公开 API、CLI 与 Web 交互；
- 更多真实文档版式与复杂表格的转换质量；
- 大容量知识库与高并发检索场景下的存储、索引策略；
- 外部 API 的内建鉴权、权限分层与安全部署体验；
- 更丰富的图谱人工校正、来源审查与协作维护界面；
- 与 Kemo 生态更多组件之间更自然的知识同步与任务协作。

如果你正在试用早期版本，欢迎提交遇到的问题、检索体验、资料格式样本，以及你希望智能体如何使用知识库的真实场景。

---

## Kemo 生态相关项目

- [kemo-agent](https://github.com/kesepain-KE/kemo-agent)
  面向个人智能基础设施的本地多用户 Agent Runtime。它负责长期记忆、上下文、子代理、工具、任务与多入口交互；可通过 kemo-graph API 使用图谱和 RAG 知识能力。

- [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api)
  Kemo 统一模型网关。它负责对接不同 LLM、Embedding、Rerank Provider，并以统一 Kemo 协议向 kemo-graph 和其他生态组件提供模型能力。

kemo-graph 与它们可以分别独立使用，也可以在同一套本地智能基础设施中各司其职。

---

## 主要维护者

[@kesepain](https://github.com/kesepain-KE)

---

## 参与贡献

kemo-graph 仍处于早期开发阶段。无论是问题报告、格式转换样本、检索质量反馈、文档改进还是功能贡献，都很欢迎。

推荐流程：

1. Fork 本仓库；
2. 创建自己的功能分支；
3. 完成修改并运行必要测试；
4. 提交 Pull Request，说明改变了什么、为什么这样改，以及验证结果。

---

## 开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。
