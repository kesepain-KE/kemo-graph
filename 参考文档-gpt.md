# kemo-graph 参考文档

本文档记录当前已经确定的项目定位、运行方式、数据结构和处理流程。
当前只考虑一个知识库场景，不设计内置的专业库管理功能。


## 一、项目定位

kemo-graph 是独立的知识图谱和 RAG 检索项目。

主要负责：

1. 接收已经转换好的文本资料。
2. 将资料整理成知识图谱数据和向量数据。
3. 查询节点、关系、节点群和相关文本。
4. 融合图谱结果与向量结果。
5. 将结构化结果返回给调用方。

与 kemo-agent 的关系：

- kemo-agent 负责决定什么时候整理、怎么查询、拿到结果后做什么。
- kemo-graph 负责存储、检索、关联、重排序和返回结果。
- 两个项目互相独立，通过 API 或工具调用通信。

当前不负责：

- PDF、Word、图片等原始文件的具体解析方式。
- 文本生成和多轮记忆。
- 专业内置分层管理。
- Neo4j、Milvus 等外部数据库服务。


## 二、知识库位置

项目当前只处理一个知识库。

运行时没有提供绝对路径：

- 使用项目工作区内默认知识库。
- 默认数据位置为项目的 data 目录。

运行时提供绝对路径：

- 将指定位置作为知识库根目录。
- 在指定位置建立 Graph 和 RAG 数据。
- 不修改项目工作区内的默认知识库。

以后如果需要按专业分层，只需要由调度方提供不同的绝对路径。
kemo-graph 本身不维护专业库列表，也不把所有专业数据强行放进一个数据库。

一个知识库的数据结构：

```text
<知识库根目录>/
├── Graph/
│   ├── graph.db
│   └── graph_meta.json
├── RAG/
│   ├── rag.db
│   ├── vector_index/
│   │   └── index.faiss
│   ├── rerank_cache.txt
│   └── rag_meta.json
└── sources.db
```

sources.db 负责管理源文档身份、路径、哈希和处理状态。
graph.db 负责知识图谱核心数据。
rag.db 负责 chunk 和原始向量数据。
index.faiss 只负责向量查询加速，可以从 rag.db 重新生成。


## 三、根路径四个启动模块

graph.py：关系图谱调度模块

[主要负责整理和查询知识图谱数据]

功能：

1. 整理指定资料，生成或更新节点、关系和节点群。
2. 查询节点、节点关系、节点分类和节点群总结。
3. 支持关系深度、正向或反向、元素类型等查询参数。
4. 未提供知识库绝对路径时使用项目默认知识库。
5. 提供知识库绝对路径时只操作指定知识库。


rag.py：向量知识库调度模块

[主要负责文本切片、向量化、相似度查询和重排序]

功能：

1. 将指定资料切分成 chunk。
2. 调用 embedding API 生成高维向量。
3. 查询相关向量数据并进行重排序。
4. 支持置信度和最大返回数量参数。
5. 未提供知识库绝对路径时使用项目默认知识库。
6. 提供知识库绝对路径时只操作指定知识库。


start.py：CLI 调度入口

[负责统一调度 graph.py 和 rag.py]

查询方式：

- 图谱
- 向量化
- 图谱+向量化

整理方式：

- 图谱
- 向量化
- 图谱+向量化

图谱和向量联合查询时，由 start.py 负责融合结果。


start_web.py：Web 启动入口

[负责知识库上传、整理、查询、配置和图谱展示]

功能：

1. 上传文本、网页、文档、代码和图片。
2. 将上传内容交给 external 层转换成文本资料。
3. 选择图谱、向量化或图谱+向量化整理方式。
4. 查看图谱和 RAG 是否落后于当前文档。
5. 查询图谱、向量数据和融合结果。
6. 查看调用日志、整理状态和错误信息。
7. 编辑 config/config.json 和 config/graph_agent.md。
8. 显示节点、关系线、节点群和引用情况。


## 四、源文档身份和状态

每个源文档使用一个看不见的唯一 source_id。
source_id 使用 UUID，不使用文件名或路径作为数据库主键。

sources.db 中建立 sources 表：

```text
source_id
original_path
relative_path
path_hash
content_hash
graph_hash
rag_hash
graph_status
rag_status
exists_status
created_at
updated_at
```

字段说明：

- source_id：文档不可见的唯一 UUID，其他数据库使用它关联源文档。
- original_path：原始文件位置。
- relative_path：当前知识库内的相对路径。
- path_hash：规范化相对路径的哈希，用于快速检测路径变化。
- content_hash：当前文本内容哈希，用于检测内容变化。
- graph_hash：最近一次成功生成图谱时使用的内容哈希。
- rag_hash：最近一次成功生成 RAG 时使用的内容哈希。
- graph_status：图谱处理状态。
- rag_status：RAG 处理状态。
- exists_status：文件当前是否存在。

处理状态：

```text
pending     等待处理
processing  正在处理
ready       处理完成
failed      处理失败
```

判断规则：

```text
content_hash != graph_hash → Graph 需要更新
content_hash != rag_hash   → RAG 需要更新
```

只整理图谱时，只更新 graph_hash 和 graph_status。
只整理 RAG 时，只更新 rag_hash 和 rag_status。
整理图谱+RAG 时，两套状态分别更新。

路径哈希和内容哈希只负责检测，source_id 才是稳定身份。

文件重命名：

- 通过 kemo-graph 或 Web 重命名时，保留 source_id，只更新路径和 path_hash。
- 用户在文件系统中手动重命名时，可以用相同 content_hash 尝试识别。
- 如果存在多份内容完全相同的文件，无法唯一判断时按新文档处理。


## 五、文件新增、修改和删除

新增文件：

1. 创建 source_id。
2. 记录路径哈希和内容哈希。
3. 根据整理类型生成 Graph、RAG 或两者。
4. 成功后更新相应处理哈希和状态。

修改文件：

1. 发现 content_hash 发生变化。
2. 只重新解析发生变化的文件。
3. 先完成节点、关系、chunk 和 embedding 的新数据准备。
4. 新数据准备成功后，再替换该 source_id 的旧数据。
5. 未变化文件完全不处理。
6. RAG 数据变化后清空重排序缓存。

删除文件：

1. sources 表将 exists_status 标记为 deleted。
2. 删除该文档的节点来源记录。
3. 删除该文档的边来源记录。
4. 删除失去全部来源的节点和边。
5. 删除该文档的 chunk 和原始 embedding。
6. 从 FAISS 物理删除该文档对应的向量。
7. 重新计算节点群。
8. 清空重排序缓存。

处理原则：

```text
新增文件 → 只新增该文件的数据
修改文件 → 只替换该文件的数据
删除文件 → 只清理该文件的数据
未变化文件 → 完全不处理
```


## 六、Graph 数据结构

graph.db 使用 SQLite。

核心表：

```text
nodes
node_sources
edges
edge_sources
groups
group_nodes
```


### 1. nodes 节点表

```text
node_id
keyword
summary
aliases
tags
ref_count
created_at
updated_at
```

字段说明：

- node_id：节点不可见的唯一 UUID。
- keyword：用户看到的节点名称。
- summary：节点的一句话摘要。
- aliases：别名列表，可以暂时使用 JSON 数组字符串保存。
- tags：分类标签，可以暂时使用 JSON 数组字符串保存。
- ref_count：引用此节点的文档数量，决定节点颜色类型显示。

节点名称和 node_id 分开保存。
节点改名不会破坏边关系，同名不同义节点也可以拥有不同 node_id。

节点是否合并不能只看名称：

1. 根据 keyword 和 aliases 查找候选节点。
2. 根据 summary 和上下文判断是否为同一个概念。
3. 相同概念复用已有 node_id。
4. 同名不同义时创建新的 node_id。


### 2. node_sources 节点来源表

```text
node_id
source_id
created_at
```

复合唯一约束：

```text
(node_id, source_id)
```

节点来源不再作为 JSON 数组写进 nodes 表。
node_sources 是节点引用关系的权威数据。
ref_count 是供 Web 快速显示使用的缓存值。

新增或删除来源时，在同一个 SQLite 事务中更新 ref_count。
没有任何来源的节点才允许删除。


### 3. edges 边表

```text
edge_id
source_node_id
relation
target_node_id
weight
support_count
created_at
updated_at
```

字段说明：

- edge_id：边不可见的唯一 UUID。
- source_node_id：源节点不可见 UUID。
- relation：关系描述。
- target_node_id：目标节点不可见 UUID。
- weight：该关系最终使用的权重，决定 Web 关系线粗细。
- support_count：支持此关系的文档数量。

唯一约束：

```text
(source_node_id, relation, target_node_id)
```

同一对节点可以拥有不同关系：

```text
RAG --包含--> 向量检索
RAG --依赖--> 向量检索
```

多个文档产生相同关系时，edges 只保存一条逻辑边，不在 Web 中画多条重叠关系线。


### 4. edge_sources 边来源表

```text
edge_id
source_id
evidence_weight
created_at
```

复合唯一约束：

```text
(edge_id, source_id)
```

字段说明：

- edge_id：对应 edges 中的关系。
- source_id：产生或支持此关系的文档。
- evidence_weight：该文档给这条关系生成的原始权重。

聚合规则：

```text
edges.weight = MAX(edge_sources.evidence_weight)
edges.support_count = 当前来源数量
```

第一阶段使用最高原始权重作为最终关系权重，不做复杂概率计算。

删除文档来源后：

1. 删除对应 edge_sources。
2. 重新计算 weight 和 support_count。
3. 没有任何来源时删除 edges 中的关系。


### 5. groups 节点群表

```text
group_id
summary
edge_count
created_at
updated_at
```

节点群表示一个互相可达的独立子图。
不同节点群之间没有关系线连接。


### 6. group_nodes 节点群成员表

```text
group_id
node_id
```

复合唯一约束：

```text
(group_id, node_id)
```

不再在 groups 中保存 node_ids JSON 数组，也不在 nodes 中保存 group_id。
group_nodes 是节点群成员关系的唯一来源。

节点群属于可重新计算的数据。
图谱变动后，可以清理旧 groups 和 group_nodes，再根据当前边重新计算。


## 七、关系描述和权重规范

relation 不设置固定枚举，允许 LLM 自由生成。

config/graph_agent.md 中进行轻度规范：

```text
关系描述建议 2～8 个字符
使用短语，不使用完整句子
优先使用简短、稳定、通用的关系表达
包含、依赖、属于、引用、实现、关联、影响、组成等只作为参考，不是限定词
遇到特殊关系时允许自由生成
```

权重范围：

```text
0～1
```

Web 显示：

- ref_count 决定节点颜色类型。
- weight 决定关系线粗细。
- support_count 可以显示关系被多少份文档共同支持。


## 八、RAG 数据结构

rag.db 使用 SQLite。

核心表：

```text
chunks
chunk_nodes
embeddings
```


### 1. chunks 文本切片表

```text
chunk_id
source_id
content
chunk_index
token_count
created_at
```

字段说明：

- chunk_id：chunk 不可见的唯一 ID。
- source_id：来源文档 ID。
- content：切片原文。
- chunk_index：在源文档中的切片顺序。
- token_count：切片 Token 数。

同一 source_id 重新处理时，只替换该文档对应的 chunk。


### 2. chunk_nodes 文本与节点关系表

```text
chunk_id
node_id
```

复合唯一约束：

```text
(chunk_id, node_id)
```

一段文本可以同时关联多个节点，一个节点也可以关联多个文本切片。
图谱命中节点后，可以通过 chunk_nodes 查找对应材料。


### 3. embeddings 原始向量表

```text
vector_id
chunk_id
source_id
vector_blob
created_at
```

字段说明：

- vector_id：向量不可见的唯一整数 ID，同时交给 FAISS 使用。
- chunk_id：向量对应的文本切片。
- source_id：向量来源文档。
- vector_blob：embedding API 返回的 float32 原始向量，以二进制形式保存。

原始 embedding 向量就是 embedding API 返回的一串浮点数：

```text
文本 → [0.124, -0.752, 0.341, ...]
```

保存原始向量的目的：

1. FAISS 索引损坏时可以从 rag.db 本地重建。
2. 更换 FAISS 索引结构时不用重新调用 embedding API。
3. 可以检查 chunk、向量和 FAISS 是否一致。
4. embedding API 停用、涨价或更换时，不会失去已经生成的旧向量。

rag.db 中的 embeddings 是核心数据。
index.faiss 只是查询加速索引。


## 九、FAISS 向量索引

当前使用方向：

```text
IndexIDMap2 + IndexFlatIP
```

IndexIDMap2 负责给每条向量绑定独立 vector_id。
IndexFlatIP 负责内积相似度查询。

删除源文档时：

1. 从 rag.db 查询该 source_id 的全部 vector_id。
2. 使用 remove_ids 物理删除 FAISS 中的向量。
3. 删除 rag.db 中对应的 embeddings 和 chunks。
4. 保存更新后的索引。

物理删除后，查询不会再召回已经删除的向量，不需要 is_deleted 软删除，也不需要扩大 top_k 后再过滤。

当前规模先使用精确索引，不使用 IVF、PQ 等复杂压缩索引。


## 十、SQLite 与 FAISS 一致性

SQLite 是权威数据，FAISS 是可重建的查询索引。
SQLite 和 FAISS 不尝试组成跨存储事务。

RAG 更新流程：

1. 调用 embedding API，先获得新向量。
2. 在 SQLite 事务中替换当前 source_id 的 chunks、chunk_nodes 和 embeddings。
3. 提交 SQLite 事务。
4. 根据 SQLite 中的有效 embeddings 更新内存 FAISS。
5. 将索引写入 index.faiss.tmp。
6. 检查向量数量、维度和 vector_id。
7. 检查成功后用临时文件原子替换 index.faiss。
8. 更新 rag_hash 和 rag_status。
9. 清空 rerank_cache.txt。

如果更新过程中异常退出：

- 下一次启动检查 SQLite 与 FAISS 的向量数量、维度和 vector_id。
- 数据不一致时，从 rag.db 的 embeddings 表重新构建 index.faiss。
- 不重新调用 embedding API。

写入期间对当前知识库使用写锁，避免同时整理和查询造成状态不一致。


## 十一、embedding API 兼容要求

embedding API 当前尚未确定，不在文档中写死厂商和模型。

provider/embedding.py 负责兼容：

- 请求格式。
- 返回字段。
- 模型名称。
- 向量维度。
- 批量 embedding。
- 是否归一化。
- 推荐距离算法。

每次调用后必须检查：

1. 返回向量数量是否等于输入文本数量。
2. 所有向量维度是否一致。
3. 是否存在 NaN 或 Infinity。
4. 当前维度是否符合 rag_meta.json。

rag_meta.json 至少记录：

```json
{
  "embedding_model": "",
  "embedding_dim": 0,
  "metric": "",
  "normalize": null,
  "rerank_model": "",
  "chunk_count": 0,
  "last_ingest_at": null,
  "last_ingest_file": null,
  "last_updated_at": null
}
```

如果 embedding 模型没有明确说明，第一阶段可以使用：

```text
向量归一化 + IndexFlatIP
```

如果模型说明要求其他距离算法，以模型说明为准。

下面任何一项发生变化，都必须整体重新 embedding：

- embedding 模型。
- 向量维度。
- 距离算法。
- 归一化方式。

网关可以统一 API 格式，但不能保证不同模型生成的向量空间兼容。


## 十二、重排序缓存

缓存文件：

```text
RAG/rerank_cache.txt
```

格式：

```text
查询关键词|排序后的 chunk_id 列表
```

示例：

```text
代码 agent|1.108 2.205 3.77
RAG 检索|1.54 2.81 3.16
```

当前只考虑一个知识库，不保存专业库 ID。
查询关键词完全一致时才允许命中缓存。

清空缓存的情况：

- 新文档完成 RAG 整理。
- 已有文档重新完成 RAG 整理。
- 文档删除。
- embedding 模型变化。
- rerank 模型变化。
- 向量距离算法或归一化方式变化。

缓存不是核心数据，删除后可以通过 rerank API 重新生成。


## 十三、Graph 与 RAG 的逻辑关联

Graph 和 RAG 物理分离，通过不可见 ID 关联。

主要关联：

```text
sources.source_id       → node_sources.source_id
sources.source_id       → edge_sources.source_id
sources.source_id       → chunks.source_id
sources.source_id       → embeddings.source_id
nodes.node_id           → edges.source_node_id
nodes.node_id           → edges.target_node_id
nodes.node_id           → chunk_nodes.node_id
chunks.chunk_id         → chunk_nodes.chunk_id
chunks.chunk_id         → embeddings.chunk_id
embeddings.vector_id    → FAISS vector_id
```

数据库内部使用 UUID 和整数 ID。
Web 和调用方默认显示 keyword、summary、relation 和原文，不显示内部 ID。


## 十四、查询流程

图谱查询：

1. 根据关键词和别名命中节点。
2. 查询正向或反向关系。
3. 根据深度扩展邻居。
4. 返回节点、关系、权重、支持数量和节点群。

RAG 查询：

1. 将查询文本转换成向量。
2. 使用 FAISS 查询 top_k 候选。
3. 根据 vector_id 从 rag.db 取出 chunk 原文。
4. 调用 rerank API 重排序。
5. 保存或命中 rerank 缓存。
6. 返回相关文本、来源和分数。

图谱+向量查询：

1. 先执行图谱节点命中和关系扩展。
2. 通过 chunk_nodes 找到相关文本。
3. 同时执行向量召回和重排序。
4. 融合图谱得分与向量得分。
5. 返回统一结构化结果。

融合公式：

```text
final_score = α × graph_score + (1 - α) × vector_score
```

α 由调用方根据查询场景传入，不在 kemo-graph 内写死。


## 十五、Web 图谱显示

Web 图谱需要显示：

1. 节点名称和摘要。
2. 节点之间的关系。
3. 节点群总结。
4. 节点引用数量。
5. 关系权重和支持文档数量。
6. 图谱整理状态和 RAG 整理状态。

显示规则：

- 节点颜色类型按照 ref_count 决定。
- 关系线粗细按照 weight 决定。
- support_count 用于显示此关系被多少份文档支持。
- 节点名字显示条件由 n5 控制。
- 鼠标悬停加载深度由 n4 控制。
- 节点支持拖动和受力调节。


## 十六、当前确定的存储结论

sources.db：

```text
sources
```

Graph/graph.db：

```text
nodes
node_sources
edges
edge_sources
groups
group_nodes
```

RAG/rag.db：

```text
chunks
chunk_nodes
embeddings
```

其他文件：

```text
Graph/graph_meta.json
RAG/rag_meta.json
RAG/vector_index/index.faiss
RAG/rerank_cache.txt
```

最终规则：

- 当前只处理一个知识库。
- 提供绝对路径时在指定位置建设知识库。
- 核心关系数据使用 SQLite。
- Graph 和 RAG 物理分离。
- 文档、节点、边使用不可见唯一 ID。
- 节点来源、边来源、节点群成员和 chunk 节点关系全部规范化存储。
- 文档新增、修改和删除都只处理该文档的数据。
- Graph 和 RAG 分别保存处理哈希和状态。
- 原始 embedding 保存在 rag.db。
- FAISS 只做查询加速，损坏后从 SQLite 本地重建。
- 关系描述由 LLM 自由生成，提示词只做轻度规范。
- ref_count 控制节点颜色类型，weight 控制关系线粗细。
