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
  <a href="version.json"><img src="https://img.shields.io/badge/version-1.1.0-00a98f" alt="version 1.1.0"></a>
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
| 多格式资料整理 | PDF、DOCX、Markdown、TXT、HTML、RST、CSV 统一导入并转为 Markdown |
| 来源追溯 | 从图谱或检索命中回到对应原文，避免只有结论没有依据 |
| 多路检索 | 图谱、向量、混合、问答与全局主题五种方式，适合不同问题 |
| 增量维护 | 文件变化后只更新受影响的数据，而不是反复重建整个知识库 |
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

```powershell
git clone https://github.com/kesepain-KE/kemo-graph.git
cd kemo-graph

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
```

在 `.env` 中至少配置网关调用密钥：

```dotenv
KEMO_API_KEY=你的-kemo-网关调用密钥
```

在 `config/config.json` 中确认网关地址与所需模型，然后构建网页前端并启动：

```powershell
cd web\frontend
npm install
npm run build
cd ..\..

python start_web.py
```

默认访问 `http://127.0.0.1:8000`。

---

## 基本使用

### 网页

启动后即可在浏览器中：上传资料、查看转换后的 Markdown 与整理状态、浏览知识图谱、选择不同方式检索、管理文档与回收站、追踪后台任务进度。

### 命令行

```powershell
# 导入文件（先不消耗模型额度）
python start.py import "E:\documents\project.pdf" --no-ingest

# 扫描并整理所有待处理文档
python start.py ingest

# 查询
python start.py query-hybrid "知识图谱如何增强检索"
python start.py query-answer "请综合图谱和原文回答"

# 状态与维护
python start.py status
python start.py list-docs
python start.py organize-graph
python start.py rebuild-all

# 检查并应用更新
python start.py update-check
python start.py update
```

### HTTP API

kemo-graph 可作为独立服务运行，向 kemo-agent 等智能体提供图谱查询、混合检索、导入与维护等端点：

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

完整请求字段、响应包络与错误码约定见 [api.md](api.md)。

---

## 数据与隐私

- 转换后的 Markdown、图谱与索引数据都保存在本地工作目录；Markdown 是事实来源，其余数据可随时重建。
- 图谱构建、Embedding 与 Rerank 会通过 Kemo 网关调用模型；处理敏感资料前，请确认网关、模型与网络边界。
- 日志只保留操作摘要与错误类别，不记录密钥、完整文档正文或完整提示词。

> **注意**：当前外部 API 没有内建应用层鉴权。默认应监听 `127.0.0.1`；若需跨设备或公网访问，请在外部部署 VPN、反向代理、TLS 或认证层，不要直接暴露未受保护的端口。

---

## 我们希望它成为什么

kemo-graph 不试图成为替代所有文件管理、所有数据库或所有搜索系统的项目。

它更希望成为 Kemo 生态中稳定的一层知识基础：资料进入系统后仍然保留来源与可追溯性；关系可以被发现，但不会脱离原文证据；文件发生变化时，系统知道该更新什么、保留什么；模型与 Provider 可以变化，但本地事实来源始终留在用户手中。

真正能陪伴长期项目的智能体，不应只拥有更长的上下文窗口，也应拥有一套能够持续理解资料、验证来源并维护结构的知识基础。

---

## 当前状态

核心闭环已经可以实际运行：统一导入、增量更新、图谱与向量检索、混合问答、安全删除、定时维护，以及本地 Web、CLI、HTTP API 三个入口和面向 kemo-agent 等智能体的外部知识服务接口。

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

## 开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。
