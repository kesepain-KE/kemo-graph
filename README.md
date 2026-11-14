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
  <a href="version.json"><img src="https://img.shields.io/badge/version-2.0.0-00a98f" alt="version 2.0.0"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license"></a>
  <a href="agent/README.md"><img src="https://img.shields.io/badge/guide-agent%20operation-0ea5e9" alt="agent guide"></a>
</p>

---

## 它对智能体的作用

kemo-graph 不是另一个负责聊天的智能体，而是**给智能体用的知识层**：把原始资料、转换后的 Markdown、图谱节点、关系证据与文本向量连在同一条可维护的链路上。

| 智能体需要 | kemo-graph 提供 |
|---|---|
| 找到资料 | 文档列表、原文读取、目录树浏览，以及全文与向量检索 |
| 知道概念之间的关系 | 图谱节点与关系，一跳关系与多跳路径，确定性 BFS 局部图谱 |
| 拿到有依据的答案 | 混合检索问答：图谱关系链 + 原文片段 + 实体与群组上下文交给模型 |
| 判断证据是否可信 | 每条关系保留来源绑定与证据权重；检索命中可回溯到原文件 |
| 资料变化后仍可信 | 增量维护：文件变化只重建受影响的数据；删除前检查共享来源 |
| 在有限上下文里省 Token | 五种检索模式按需选择，先拿结构再取原文，而不是把整篇塞进上下文 |

当前版本 **2.0.0**。

## 智能体如何操控它

外部智能体（kemo-agent 或其他任何框架）通过 HTTP API 操控本服务，不依赖特定智能体客户端。

**操控手册入口：[`agent/README.md`](agent/README.md)**

该目录按任务拆分：连接与服务边界、检索与问答、文档与目录管理、图谱读取与写入、构建与维护、端点速查。开始前先读连接说明，再只读当前任务对应的那一篇。

完整请求字段、响应结构与错误码见 [`api.md`](api.md)。

## Kemo 生态相关项目

- [kemo-agent](https://github.com/kesepain-KE/kemo-agent) — 面向个人智能基础设施的本地多用户 Agent Runtime，可通过 kemo-graph API 使用图谱与 RAG 知识能力。
- [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api) — Kemo 统一模型网关，以统一协议向生态组件提供 LLM、Embedding、Rerank 模型能力。

它们可以分别独立使用，也可以在同一套本地智能基础设施中各司其职。

---

## 开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。
