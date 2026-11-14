# kemo-graph 文本匹配与实体对齐重构方案

> 交付对象：编程智能体。请按阶段顺序执行，每阶段独立可验证、可回滚。
> 实施校正：RapidFuzz 仅做筛选上界；缓存按完整节点内容失效；整理粗筛使用原评分公式的安全下限。详见阶段 2—4 与文末验收结果。
> 前置阅读：`core/graph_engine.py`、`core/graph_organizer.py`、`core/entity_extractor.py`、`core/fulltext.py`、`core/graph_search.py`。
> 约束：保持离线优先、SQLite 单机、无运行时下载；不改变 `nodes/edges/node_sources/edge_sources/groups/chunk_nodes` 表结构与 API 返回结构。

---

## 阶段 0：让测试能跑起来（阻塞项，必须先做）

当前 `tests/test_graph_search.py` 无法收集，全部测试挂掉：

```
FileNotFoundError: 'E:\code\kemo-graph\version.yaml'
tests/test_graph_search.py:10: from api import create_app
api/__init__.py:68: app = create_app()          # 模块导入时立即构造 app
update/updater.py:142: payload = yaml.safe_load((root / VERSION_FILE).read_text(...))
```

现状：仓库有 `version.json`（内容 `{"version": "1.6.0"}`），但 `update/updater.py` 读的是 `version.yaml`。

**要求**

1. 确认这是本工作副本缺失还是版本迁移未完成。如果项目已切到 `version.json`，改 `update/updater.py` 的 `read_local_version()` 与 `VERSION_FILE` 常量；如果 `version.yaml` 才是权威，补齐文件并删除 `version.json` 的歧义。
2. 顺带处理 `api/__init__.py:68` 的模块级 `app = create_app()`：它让任何 `from api import ...` 都触发全量初始化（读版本、建目录）。建议改为惰性工厂，或在此行加 try/except 让测试可导入。**此项可选**，但如果改，需保证 `uvicorn api:app` 等既有入口不变。
3. **验收**：`python -m pytest tests/ -q` 能开始执行（允许有既有失败，但不得有 collection error）。记录基线失败清单，后续阶段比对用。

---

## 阶段 1：统一归一化，修复 casefold 不一致（纯 bugfix）

### 问题

同一个归一化逻辑存在四份实现，其中一份**漏了 `casefold()`**：

| 位置 | 实现 |
|---|---|
| `core/graph_engine.py:568` `_match_key()` | NFKC + casefold + split |
| `core/graph_draft.py:304` `_key()` | NFKC + casefold + split |
| `core/graph_organizer.py` `_key()` | NFKC + casefold + split |
| `core/entity_extractor.py:221` `_normalize()` | **NFKC + split（无 casefold）** |

后果：`GPT` 与 `gpt` 在实体抽取阶段是两个不同实体，在节点匹配阶段却是同一个。抽取产出的 `normalized` 值大小写敏感，写入图谱后与匹配逻辑不一致。

### 改动

1. 新建 `core/text_norm.py`，导出单一函数：

```python
def norm_key(value: str) -> str:
    """NFKC 归一 + casefold + 空白折叠。所有文本匹配的唯一入口。"""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
```

2. 把上述四处替换为 `from .text_norm import norm_key`。
   - `graph_engine._match_key` → 直接删除，调用点改 `norm_key`
   - `graph_draft._key`、`graph_organizer._key` → 同上
   - `entity_extractor._normalize` → **注意语义差异**：它当前不做 casefold，且被用于 `Entity.text` / `Entity.normalized` / `Entity.type` 的落库值。**不要盲目全替换**：
     - 用于**去重与比较**的调用（`_deduplicate` 的 key、`_extract_by_rule` 的 stopword 判断、alias 去重比较）→ 改用 `norm_key`
     - 用于**保存展示值**的调用（构造 `Entity(text=..., normalized=...)`）→ 保留原有 `_normalize`（NFKC + 空白折叠，不改大小写）
   - 建议把 `entity_extractor._normalize` 重命名为 `display_normalize`，让"归一化"和"匹配键"两个概念在命名上分开。

3. **验收**
   - `norm_key("ＧＰＴ-4o") == norm_key("gpt-4o")` 为真（全角/大小写归一）
   - 现有 `tests/test_graph_search.py`、`tests/test_query_planning.py`、`tests/test_phase*.py` 中涉及实体抽取的断言不回退
   - 新增 `tests/test_text_norm.py` 覆盖：全角半角、大小写、连续空白、中文无变化、`"武汉"` 与 `"武 汉"` 保持不等（不应吞掉空格差异）

---

## 阶段 2：`graph_engine` 匹配热路径优化（最大收益，风险最低）

### 2a. 消除重复归一化（先做这个，不引入依赖）

`core/graph_engine.py:454 _node_match_score` 对**每个 entity × 每个 node** 都重新构造三个集合：

```python
node_keyword = _match_key(node.keyword)                        # 每对都算
node_aliases = {_match_key(a) for a in node.aliases}           # 每对都算
descriptive_terms = {_match_key(v) for v in [node.summary, *node.tags]}  # 每对都算
```

`_match_nodes`（`:195`）的调用形态是 `for entity in entities: for node in nodes:`，所以这是 **E × N** 次集合构造 + 6 次 `NFKC` 归一。

**改法**：在 `_match_nodes` 读节点时一次性预计算，随 `_Node` 一起传递。

```python
@dataclass(frozen=True)
class _NodeKeys:
    keyword: str
    aliases: frozenset[str]
    descriptive: frozenset[str]      # summary + tags
```

`_node_match_score(entity, node, keys)` 只接收预算好的键。E×N 次归一化降为 N 次。

**验收**：所有现有匹配行为不变（这是纯等价改写）。用一个临时 benchmark 脚本对比 N=2000、E=5 时的耗时。

### 2b. 使用 RapidFuzz 上界筛选，保留最终评分

`SequenceMatcher.ratio()` 与 `rapidfuzz.fuzz.ratio()` 并不等价：前者使用匹配块启发式，后者是归一化 Indel/LCS 相似度。对 304 组固定样本实测，144 组差值达到或超过 0.02，最大差值为 1/3，不能直接替换。

实现保留 `SequenceMatcher` 的最终评分，只用 RapidFuzz 作为上界：上界不足以达到置信度阈值的词对无需继续计算。阈值筛选同时保留可能达到 0.85 的强匹配，因为这些匹配不受实体抽取置信度衰减。

**验收**

- `tests/test_match_score_equivalence.py` 固定记录上述差异，并验证筛选上界和原始评分的关系。
- 最终分数、摘要兜底、强匹配和置信度边界与原实现保持一致，不放宽误差。
- Benchmark 分别记录首次构建索引与缓存命中的耗时。

### 2c. `requirements.txt` 增加依赖

```
rapidfuzz>=3.10,<4.0
```

rapidfuzz 全平台有预编译 wheel（win/mac/linux，含 ARM），满足"离线优先、无需编译工具"的既有约束。同步检查 `deploy/pack.py` 是否需要把新依赖纳入打包清单。

**回滚点**：阶段 2 全程不改变任何数据，`git revert` 即可。

---

## 阶段 3：实体候选生成换 AC 自动机

### 设计前提（重要，别做错）

当前流程是 **query → LLM 抽 Entity → Entity 逐个匹配全部 node**。所以 AC 自动机的定位是**候选生成器**，不是替代匹配逻辑：

```
raw_query + 所有 entity 的 (text, normalized, *aliases)
        ↓  AC 自动机扫描（词典 = 全部 node 的 keyword + aliases）
    候选 node 集合（高置信）
        ↓  现有 _node_match_score 只在候选集上跑（精确重排）
    最终 _MatchedNode
```

这样做的两个收益：
1. **速度**：缓存复用后的字面候选扫描与查询长度、命中数相关。完整查询仍需读取节点快照，并处理模糊筛选与必要的描述兜底，不宣称端到端为 O(query 长度)。
2. **召回**：现在如果 LLM 没抽出某个实体，即使它在 query 里字面出现也匹配不到。AC 直接扫原文能兜住这种情况。

### 实现

新建 `core/node_index.py`：

```python
class NodeMatcher:
    """节点关键词/别名的 AC 自动机索引，供候选生成使用。"""
    def __init__(self, nodes: Iterable[tuple[str, Iterable[str]]]): ...
    def match_terms(self, texts: Iterable[str], *, include_containing: bool = True) -> dict[str, list[tuple[int, int]]]:
        """返回 {node_id: [(start, end), ...]}，保留所有重叠匹配。"""
```

- 依赖 `ahocorasick_rs>=0.22`（Rust，全平台 wheel，`find_matches_as_indexes(text, overlapping=True)`）。
- **词典键必须用 `norm_key()` 归一化后的小写串**，扫描前对输入文本同样归一化。
- 缓存：使用完整、不可变的节点行快照作为 `lru_cache(maxsize=4)` 的键。`max(rowid)/count(*)` 无法发现节点内容修改，不能作为版本依据。快照直接来自当前连接，可正确覆盖未提交更新、回滚、提交及数据库替换，且不持有数据库句柄。
- **重叠匹配处理**：`知识图谱` 与 `图谱` 可能同时命中同一位置区间。保留全部候选交给现有 scorer 打分即可，不要在这里做最长优先（会让 `图谱` 这类下位词永远匹配不到）。

### 接入 `_match_nodes`

1. 读取节点快照并复用预计算键与 AC 索引。
2. 对每个实体的原文、规范名、别名生成双向包含候选，同时用 RapidFuzz 矩阵补齐可能达到 0.85 的模糊候选。
3. 用原评分器重排候选。存在强匹配时仍抑制弱摘要命中；没有强匹配时继续扫描完整节点集，以保留摘要、标签与较低阈值下的模糊召回。
4. `_SUMMARY_CONTAINS_MATCH_SCORE = 0.78` 和 `_MAX_SUMMARY_FALLBACK_NODES = 20` 保持原值、排序与行为。
5. 原始查询单独进行正向字面扫描，补回抽取器遗漏的节点；不反向用整句扩展节点词典，也不对原始整句额外做模糊召回。
6. 查询拒绝 NUL 和超过 1000 字符的输入，空白查询仍拒绝，纯标点不生成字面锚点。

### 验收

- `tests/test_graph_search.py`、`tests/test_query_planning.py`、全部 `tests/test_phase*.py` 通过。
- 新增 `tests/test_node_matcher.py`：
  - 简称/全称/别名互相命中（`知识图谱` / `图谱` / `语义网络`）
  - 重叠匹配全部返回
  - 中英混排（`SQLite FTS5`）、带连字符（`GPT-4o`）命中
  - 缓存失效：节点新增后立即命中
  - 空 query、纯标点、1001 字符 query 的边界（与 `fulltext.py:83` 的上限保持一致）
- 新增 benchmark：N=2000/5000/10000，E=1/5/10，对比改造前后耗时；**如果 5000 节点下收益不足 2 倍，说明候选集太散，需回头调 AC 的最小匹配长度**（参考现有 `_MIN_PARTIAL_MATCH_LENGTH = 2`）。

**回滚点**：基准脚本从固定提交 `4a03285` 加载原实现作对照。等价性测试与全量回归通过后，生产代码只保留新实现；可按阶段提交回滚。后端 CI 获取完整 Git 历史，保证固定对照提交可读取。

---

## 阶段 4：`graph_organizer._find_candidates` 的 O(n²) 两两比较

### 问题

`core/graph_organizer.py` 的 `_find_candidates`：

```python
for left_index, left in enumerate(nodes):
    for right in nodes[left_index + 1:]:              # O(n²)
        lexical = max(
            (SequenceMatcher(None, lt, rt).ratio()
             for lt in left["terms"] for rt in right["terms"]),   # 内层再乘
            default=0.0,
        )
```

`MAX_ORGANIZER_CANDIDATES = 200` 只截断输出，**没有减少任何计算量**。5000 节点即 1250 万对，每对跑若干次纯 Python `SequenceMatcher`。

现在 `data/Graph/graph.db` 是空的（`nodes = 0`），所以这个性能问题还没暴露，但一旦导入真实语料就会卡住。

### 改法

1. 用 `norm_key(term) -> 节点集合` 的哈希分组生成精确候选，不再逐对计算字符串相似度。按原排序保留前 200 条；已有 200 条满分候选时可直接返回。
2. 所有关键词和别名参与词法筛选，不能只取最长代表词，否则会漏掉别名匹配。
3. 使用 `rapidfuzz.process.cdist(..., scorer=fuzz.ratio, dtype=np.float64, workers=-1)` 计算词法上界。按 64 个节点分块，避免分配完整 N×N 浮点矩阵。
4. 粗筛下限从原公式推导：`max(0, min(threshold, (threshold - 0.35) / 0.65))`。因为摘要、邻接项最多补足 0.35，此下限不会丢掉能被加权公式挽回的候选；不采用可能漏召回的经验倍率。
5. 仅对保留下来的节点对计算原始 `SequenceMatcher` 词法分、摘要分与邻接 Jaccard，最终仍用 `max(lexical, lexical * 0.65 + summary * 0.2 + adjacency * 0.15)`。
6. 保留原有定向评分、稳定排序和 200 条上限；摘要归一化只计算一次，邻接签名逻辑不变。

### 验收

- **必须新写 `tests/test_graph_organizer_candidates.py`**（当前**完全没有** graph_organizer 的测试，这是个缺口）：
  - `exact_term` 识别：同 keyword、同 alias、keyword 与 alias 交叉
  - `semantic_similarity` 阈值边界（`graph_organize_similarity = 0.86`）
  - summary 相似度与 adjacency 的加权公式 `lexical * 0.65 + summary * 0.2 + adjacency * 0.15` 数值不变
  - 候选排序键 `(-score, left_keyword, right_keyword, left_node_id, right_node_id)` 稳定
  - 200 条截断生效
- Benchmark：N=1000/5000，记录改造前后耗时。**目标：5000 节点从分钟级降到秒级。**
- **等价性验证**：构造 500 节点的合成数据集，对比改造前后的候选列表（`left_node_id, right_node_id, reason` 集合），要求完全一致；`score` 允许 ±0.02 误差，超差需记录样本。

**回滚点**：本阶段不触碰数据库写入，只改候选计算。

---

## 阶段 5：`compile_fulltext` 过滤虚词与纯符号（半小时，纯收益）

### 问题（已实测）

```
Q: GPT-4o 和 o1-preview 有什么区别
expr: gpt* AND "-" AND "4"* AND o* AND "和" AND "o1" AND "-" AND preview* AND "有" AND "什么" AND "区别"
hits: 0
```

三个叠加原因：虚词进了 AND；连字符 `-` 被当成必须出现的独立 token；`gpt4o` 与 `gpt-4o` 因符号 token 而互不命中。

而 `core/sqlite_simple/dict/stop_words.utf8`（1532 行，含 `the/a/an/that/什么/如何`）**从未被加载**——`simple_tokenizer.cc` 的 `tokenize()` 只跳过 `SPACE` 类别，那份表是 cppjieba `KeywordExtractor` 专用的死代码。

### 改动

`core/fulltext.py:82 compile_fulltext`：

```python
def compile_fulltext(database, query):
    if "\0" in query or len(query) > 1000:
        raise ValueError("fulltext 查询不能包含 NUL，且最多 1000 个字符")
    expression = database.execute("SELECT jieba_query(?,0)", (query,)).fetchone()[0]
    return _prune_expression(expression)
```

`_prune_expression` 按 `AND` 拆分（**注意不能拆引号内的 `AND`**，虽然 `jieba_query` 的输出结构简单，仍建议用状态机而非 `split(" AND ")`），然后：
- 丢弃完全由标点/符号构成的项（如 `"-"`）
- 丢弃纯虚词项（自建小型中文/英文虚词表更可控，**不要**去解析 `stop_words.utf8`，它格式是 cppjieba 专用且含大量乱码字符）
- 全部被丢弃时返回空串（`search_nodes` 已有 `if not expression: return result` 的处理）

### 关键约束与风险

`tests/test_graph_search.py:44-54` 断言了具体的 FTS 命中行为：

```python
page = service.graph.search_nodes("知识 图谱", limit=1)
assert page["engine"] == "sqlite-fts5/simple" and page["total"] == 2
assert service.graph.search_nodes("语义检索")["nodes"][0]["node_id"] == "c"
assert service.graph.search_nodes('" OR * NOT')["nodes"] == []
```

过滤后这些断言可能变化。**逐条核对，如果行为变化是变好（比如原本 0 命中变成有命中），要更新断言并在 PR 说明里写清原因；如果变差，回滚。**

`'" OR * NOT'` 这条尤其要注意：它现在返回空，过滤后仍必须返回空，且不能抛 FTS5 语法错误。

### 验收

- 上述 4 条断言逐个确认（改或不改都要有理由）
- 新增 `tests/test_fulltext_expression.py`：直接测 `compile_fulltext` 输出，不依赖数据库内容
  - `"GPT-4o 和 o1-preview 有什么区别"` → 不含 `"-"`、不含 `"和"`
  - `"知识图谱"` → `"知识" AND "图谱"`（不变）
  - 全虚词查询 → 空串
  - 1001 字符 / 含 `\0` → `ValueError`（不变）

---

## 阶段 6（仅评估，不默认执行）：FTS tokenizer 是否替换

**默认不做。** 前五阶段完成后，先用真实语料跑一轮检索评测，再决定。

需要知道的事实：

| 方案 | 事实 | 结论 |
|---|---|---|
| FTS5 内置 `trigram` | 少于 3 个 Unicode 字符的子串**不匹配任何行** | 中文双字词（`图谱`/`检索`/`实体`）大面积失效，不可用 |
| `sqlite-better-trigram` | 每个 CJK 字符独立 token，双字能命中 | 需自行 `make loadable`（LEMON + Tcl），无 7 平台预编译 release，与现有分发模式冲突 |
| 索引侧预切分（词级索引 + 词级查询） | 最干净，`"知识图谱"` 变真短语匹配 | 需 `pip install jieba`，破坏 kemo-agent 侧"不需要额外 pip 分词包"的声明；需自己维护位置映射，`match_positions()` 用不上 |
| LanceDB | 原生 FTS 支持 `base_tokenizer="jieba/default"`，且能在一条查询里融合 BM25 + 向量 | 换 Arrow 存储格式，与现有 SQLite 三库 + 跨库事务模型冲突。**若 `chunk_nodes` 规模撑爆 SQLite 才考虑** |
| 保持 `simple` | 索引字级（召回高）+ 查询词级（精度可控），不依赖索引-词典同步 | **默认保留**。阶段 5 的过滤已消除主要症状 |

**决策门槛**：如果真实语料评测里 `search_nodes` 的 Top-3 命中率 < 80%，再启动本阶段；否则不动。

---

## 明确不做的事

1. **不引入 LightRAG / GraphRAG / nano-graphrag**。理由：
   - LightRAG 四套默认后端（`JsonKVStorage`/`NanoVectorDBStorage`/`NetworkXStorage`/`JsonDocStatusStorage`）全在内存，官方明说只适合小规模测试；上生产要 Postgres + Neo4j，破坏离线单机定位。
   - 现有 `sources.db` 的 `graph_status/rag_status/graph_hash/rag_hash` 状态机 + `node_sources.content_hash` 一致性校验，在这类框架里没有对应物，迁移等于重写。
   - `config/` 下四份可调提示词（`chunking_prompt.md`/`graph_extract.md`/`graph_organizer.md`/`query_planning_prompt.md`）是核心资产，框架会把它们塞进固定 pipeline。
2. **不引入 Kuzu**。2025-10-10 仓库 archived（Kùzu Inc. 被 Apple 收购），0.11.3 为终版，仅剩社区 fork（Ladybug/Bighorn/Vela）。MIT 许可虽可自维，但不适合作为长期底座。
3. **不引入 HanLP / LTP / THULAC**。依赖 torch 或 JVM，违背"运行时零下载、预编译库随仓库"的前提。
4. **不用 flashtext**。只返回最长匹配、要求词边界，CJK 下词边界概念失效（`知识图谱` 嵌在 `知识图谱构建` 里匹配不到），项目基本停更。
5. **不直接用 `fuzz.ratio` 或 `fuzz.WRatio` 替换最终评分**。保留 `SequenceMatcher` 的判定边界，`fuzz.ratio` 只作为不会漏召回的筛选上界。

---

## 通用要求

- **每阶段独立提交**，commit message 写清"改了什么/为什么/验收方式"。
- **不修改任何数据库 schema**，不写数据迁移脚本。本方案全程是计算逻辑替换。
- **每个阶段先跑一遍全量测试记录基线**，改造后对比。阶段 0 记录的基线失败清单是唯一参照，**不得引入新的失败**。
- **benchmark 脚本放 `tests/bench/`，不进 pytest 默认收集**（命名 `bench_*.py`），结果贴进 PR 描述。
- 涉及 `_match_nodes` 和 `_find_candidates` 的等价性验证，若发现无法解释的结果差异，**停下来报告**，不要自行放宽断言。
- `pyproject.toml` 是 `kemo-graph-markitdown` 子包的，与主应用无关；新依赖加在 `requirements.txt`。

---

## 收益预估

| 阶段 | 收益 | 风险 |
|---|---|---|
| 0 | 测试可执行（当前完全阻塞） | 低 |
| 1 | 修 `GPT`/`gpt` 实体分裂 bug；归一化逻辑从 4 份收为 1 份 | 低 |
| 2a | E×N 次归一化降为 N 次 | 极低（纯等价改写） |
| 2b | 用编译实现的上界跳过无效词对，保留原评分 | 低（已量化直接替换的差异） |
| 3 | 精确匹配 O(N) → O(len)；补 LLM 漏抽的召回;5000 节点下预期 10× 以上 | 中（候选集策略需调） |
| 4 | 5000 节点从分钟级降到秒级 | 中（需等价性验证） |
| 5 | 修复自然语言长句 0 命中 | 低 |


## 实施与验收（2026-11-14）

阶段 0—5 已完成。当前工作副本以 `version.yaml` 为权威版本文件，值为 `2.0.0`；测试可以正常收集执行。阶段 6 保持默认不执行，继续使用已随项目提供的 simple tokenizer，未开展真实语料 Top-3 命中率评测。

### 回归验证

| 检查点 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| 接续工作时的全量基线 | 408 | 5 | 0 |
| 候选索引完成后 | 423 | 5 | 0 |
| 整理优化与全文过滤的最终全量回归 | 433 | 5 | 0 |

最终命令：`python -m pytest tests/ -q`，耗时 175.60 秒，另有 2 个子测试通过。唯一警告来自已有 FastAPI/Starlette 测试客户端的弃用提示。

- 对 304 组固定词对量化 RapidFuzz 和 SequenceMatcher 的差异；保留原始评分，并验证多种置信度与阈值边界。
- AC 检索覆盖重叠词、共享别名、双向包含、中英混排、全角、连字符和单字边界；原始查询可以补回抽取器遗漏的字面节点。
- 节点索引覆盖插入、内容修改、未提交修改、回滚、删除及调用方修改返回数组，确保缓存不会返回过期数据或共享可变状态。
- 500 节点的整理对照同时包含精确候选与语义候选，候选列表、分数及顺序与固定原实现完全一致。另验证短别名、摘要及邻接关系的加权救回、阈值相等与 200 条上限。
- 真实 bundled tokenizer 验证模型对比问句、纯符号、虚词、带引号的 AND、转义双引号、C++、C# 和非法输入。既有知识图谱搜索命中断言保持不变。

### 节点匹配性能

固定数据种子为 42，节点名为 14 字符随机词，每个节点另有一个别名。对照为提交 `4a03285` 中的原始实现。以下均为本机合成数据结果；首次耗时包含索引构建，复用耗时取 3 次中的最小值，不包含 LLM 抽取或网络请求。

| 节点数 | 实体数 | 原实现/秒 | 首次/秒 | 复用/秒 | 复用加速 | 结果一致 |
|---|---:|---:|---:|---:|---:|---|
| 2000 | 1 | 0.119170 | 0.043455 | 0.003815 | 31.24× | 是 |
| 2000 | 5 | 0.485134 | 0.045067 | 0.004769 | 101.74× | 是 |
| 2000 | 10 | 0.940673 | 0.047000 | 0.005967 | 157.66× | 是 |
| 5000 | 1 | 0.294652 | 0.195189 | 0.016600 | 17.75× | 是 |
| 5000 | 5 | 1.704409 | 0.141322 | 0.019743 | 86.33× | 是 |
| 5000 | 10 | 3.301521 | 0.187313 | 0.024023 | 137.43× | 是 |
| 10000 | 1 | 0.894730 | 0.339807 | 0.033667 | 26.58× | 是 |
| 10000 | 5 | 3.451323 | 0.388090 | 0.040002 | 86.28× | 是 |
| 10000 | 10 | 6.603850 | 0.414806 | 0.046463 | 142.13× | 是 |

重现：`python tests/bench/bench_text_matching.py --nodes 2000 5000 10000 --entities 1 5 10`。

### 整理候选性能

| 节点数 | 原实现/秒 | 新实现/秒 | 说明 |
|---|---:|---:|---|
| 1000 | 87.543454 | 0.113646 | 输出一致，约 770.32× |
| 5000 | 120 秒内未完成 | 0.915656 | 原实现子进程达到上限，未推算其完整耗时或加速倍数 |

重现：`python tests/bench/bench_text_matching.py --nodes 1000 5000 --organizer --baseline-timeout 120`。500 节点完整等价性由 `tests/test_graph_organizer_candidates.py` 独立验证，5000 节点超时不代表已完成同规模的原实现对照。

### 交付范围

实现没有修改数据库表结构或 API 返回结构。原评分、0.78 摘要分、20 条摘要兜底上限、整理的加权公式与稳定排序保持原语义。新依赖通过 `requirements.txt` 安装，运行时不下载模型或词典。

各实现阶段使用独立本地提交。为使全文过滤可独立回滚，先单独记录了工作副本中原有的离线加载器，再提交过滤逻辑。其他较早的配置、文件管理和前端改动保留在工作区，未推送远程。
