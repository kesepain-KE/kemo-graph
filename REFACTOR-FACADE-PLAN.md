# kemo-graph 核心模块门面化重构方案

> 交付对象：编程智能体。按批次顺序执行，**每个阶段结束时全量测试必须全绿才可进入下一阶段**。
> 前置基线：`python -m pytest tests/ -q` → `374 passed, 5 skipped, 0 failed`（2026-11-13 实测，耗时约 180s）。
> 本方案全程**只动模块组织与依赖方向，不改任何业务逻辑、数据库 schema、HTTP 合约、CLI 参数**。

---

## 0. 问题陈述（已实测，非推测）

### 0.1 扁平化现状

`core/` 包根目录平铺 **41 个模块，约 13,800 行**，仅 `ingestor/`（7 文件）与 `services/`（6 文件）两个子包。

| 目录 | 模块数 | 行数 | 形态 |
|---|---|---|---|
| `core/` 根 | 41 | ~13,800 | 全部平铺 |
| `core/ingestor/` | 7 | ~2,565 | 唯一已正确分包 |
| `core/services/` | 6 | ~1,300 | 第二套门面，纯转发 |

### 0.2 双层门面互相弹球（最严重）

`KnowledgeBaseService` 由 4 个 Mixin 拼成（`KnowledgeDocumentsMixin` 850 行/15 方法、`KnowledgeGraphMixin` 789/21、`KnowledgeRetrievalMixin` 259/14、`KnowledgeStatusMixin` 308/8），同时持有 4 个 `core/services/*` 域对象。

**但域对象只有壳，实现在 Mixin 里，Mixin 又反向调用域对象**：

```
core/services/graph.py:26    GraphService.get_node()        # 真实 SQL 实现
core/knowledge_graph.py:111  _get_node_impl() → self.graph.get_node()   # 又转回去
core/knowledge_base.py:...   KnowledgeBaseService.get_node() → ?        # 已无调用者
```

- `services/document.py` 127 行 / 12 方法：**零业务逻辑，纯 `owner._xxx_impl()` 转发**
- `services/maintenance.py` 34 行 / 8 方法：**同上**
- `services/retrieval.py:334` 在**函数体内**写 `from ..knowledge_base import KnowledgeBaseProcessingError`，专为绕开循环导入 —— 这是该层拆分未完成的直接证据
- `delete_document` 全项目出现 **6 处**；`get_node`/`delete_node`/`get_relation`/`delete_relation` 各 **3~4 处**

### 0.3 拆分后旧代码没删：函数体逐字节重复

| 函数 | 重复位置 |
|---|---|
| `_build_answer_context`（45 行） | `knowledge_support.py` ↔ `services/retrieval.py` |
| `_dict_items` / `_clip_context_text` | 同上 |
| `_parse_json_list` / `_relation_row` | `knowledge_support.py` ↔ `services/graph.py` |
| `_fetch_one` / `_fetch_all` / `_row_dict` / `_transaction` / `_escape_like` | `provider/tools/delete_tools.py` ↔ `graph_tools.py` |
| `_now_iso` | **8 个模块** |
| `_required_text` | 6 处 |
| `_write_json_atomic` / `_write_bytes_atomic` | 5 处 / 3 处 |
| `_elapsed_ms` | 4 处 |

### 0.4 垃圾抽屉

- `knowledge_support.py`：615 行、**26 个顶层函数、0 个类**，混了 LLM 封装、文档转换、路径安全、哈希快照、行序列化、答案上下文。被 6 个模块 import。
- `ingestor/_utils.py`：136 行、15 个函数，其中 `_now_iso`/`_required_text`/`_write_json_atomic` 本该在公共层，却被困在包内。

### 0.5 上帝类

| 文件 | 行数 | 单体 |
|---|---|---|
| `rag_engine.py` | 927 | `RAGEngine` **33 方法 / 859 行** |
| `graph_organizer.py` | 912 | `GraphOrganizer` 366 行 + 13 个模块级函数 |
| `ingest/__init__.py` | 718 | `Ingestor` **32 方法 / 584 行** |
| `config.py` | 481 | 9 个类混 schema/默认值/迁移/读写/路径解析 |
| `db.py` | 786 | 26 个函数混路径/连接/schema/meta/迁移 |

### 0.6 `api/` 未分包

- `routes.py`：778 行、**53 个路由函数**，一个文件塞 status / ingest / query×5 / cache×3 / documents×6 / graph / nodes / relations / visualization×4 / config×3 / update×3 / jobs×5 / maintenance×3 / stores×6
- `schemas.py`：526 行、51 个类，其中 `Store*` 占 30+
- 项目已有 `build_routes.py` / `library_routes.py` / `log_routes.py` 的正确先例，只是 `routes.py` 没跟

### 0.7 两个结构性阻塞

**a) `api/__init__.py` 末尾模块级 `app = create_app()`**
任何 `from api import ...` 都触发全量初始化（读版本、建目录、起 job manager），重构期每次改文件都吃这个代价。

**b) 真实循环依赖 `core.ingest` ↔ `provider.tools`**

```
core/ingestor/_graph_build.py:176     from provider.tools import get_graph_tools, ...   # 函数体内，为绕循环
provider/tools/delete_tools.py:19     from core.ingestor import Ingestor               # 模块顶层
```

另有一条镜像边：`core/knowledge_support.py:22` → `provider.tools.document_tools` → `core.knowledge_models`。
**文档转换的实现放在了 `provider`，却由 `core` 依赖**，方向是反的。

---

## 1. 目标结构

```
core/
├── __init__.py            # 全项目唯一对外清单（≤24 符号）
├── common/                # 基础设施，零业务依赖，可被 provider 安全引用
│   ├── __init__.py        # 门面
│   ├── time.py            # now_iso, elapsed_ms
│   ├── jsonio.py          # write_json_atomic, write_bytes_atomic, load_json_string_list
│   ├── text.py            # required_text, optional_text, parse_json_list, clip_text,
│   │                      # escape_like, unique_texts
│   ├── fs.py              # validated_path, safe_relative_path, is_within, sha256_of
│   └── sql.py             # fetch_one, fetch_all, row_dict, transaction
├── settings/              # 原 config.py（481 行）
│   ├── schema.py          # AppConfig 及 8 个配置类
│   ├── defaults.py        # default_config_dict, write_default_config
│   ├── loader.py          # load_config, write_config, deep_merge, 迁移
│   └── errors.py          # ConfigLoadError
├── observability/         # logger / log_viewer / terminal_logging / read_cache
├── storage/               # 原 db.py + locks.py + portable_store.py
│   ├── paths.py           # DatabasePaths, get_database_paths
│   ├── schema.py          # initialize_databases + 6 个 _ensure_*
│   ├── connections.py     # connect_sources/graph/rag/search_cache
│   ├── meta.py            # read/write_graph_meta, read/write_rag_meta
│   ├── locks.py
│   └── portable/          # 原 portable_store.py（564 行）
├── documents/             # library / preview / build / formats / conversion
├── graph/                 # engine / matching / organizer / candidates / draft /
│   │                      # search / visualization / entity
├── rag/                   # engine / index / vectors / fusion / lexical /
│   │                      # auxiliary / text / errors / query_planner
├── ingest/                # pipeline / scan / graph_build / prompt / rag_build /
│   │                      # delete / file_map / chunker/ / progress / errors
├── retrieval/             # hybrid / query_progress / query_logging / cache/
├── maintenance/           # jobs / rebuild / scheduler / source_sync
└── service/               # 单一入口
    ├── facade.py          # KnowledgeBaseService（薄，每方法一行委托）
    ├── context.py         # ServiceContext（原 ServiceOwner Protocol 实体化）
    ├── documents.py       # DocumentService（真实现）
    ├── graph.py           # GraphService（真实现）
    ├── retrieval.py       # RetrievalService（真实现）
    ├── maintenance.py     # MaintenanceService（真实现）
    └── errors.py          # 全部异常类，从 knowledge_models.py 迁入
```

**11 个新包名与现有模块名全部无冲突**（`settings` 避开 `config.py`、`ingest` 避开 `ingestor/`、`service` 避开 `services/`），因此**不需要在迁移过程中处理同名文件/目录冲突**。这是本方案能分阶段全绿推进的前提。

### 1.1 层序（依赖只能向下）

```
L0  common                     ← 零内部依赖
L1  settings, observability
L2  storage
L3  documents, graph, rag
L4  ingest, retrieval        ← ingest 可依赖 graph/rag/documents/storage
L5  maintenance
L6  service                  ← 唯一入口，可依赖全部下层
```

`core/common` 严禁 import 任何 `core` 其他子包，也严禁 import `provider` / `api`。
`core.graph` / `core.rag` / `core.ingest` / `core.documents` 严禁 import `core.service`（禁止反向依赖上层）。

### 1.2 门面规则

每个子包的 `__init__.py` 是**薄门面**：

- 只做 `from .xxx import Yyy` + 显式 `__all__`
- **不写任何逻辑、不建单例、不做模块级副作用**
- 导出预算：`core` ≤24，`core.graph` ≤12，`core.rag` ≤12，`core.ingest` ≤10，`core.storage` ≤12，其余 ≤8
- 门面之外的文件默认是内部实现，包外不得直接 import（由 Stage 0 的检查器强制）

### 1.3 `core/service` 的单一入口契约

`facade.py` 中每个公开方法**函数体不得超过 3 行，且只能是参数规范化 + 一次委托**：

```python
def get_node(self, node_id: str) -> dict[str, Any]:
    return self.graph.get_node(node_id)
```

`_xxx_impl` 私有方法**全部删除**（已核实仅 `core/services/` 与 `tests/test_knowledge_base_services.py` 引用，包外无人用）。
异常类与常量由 `core/service/errors.py` 持有，`core.service` 门面统一导出。

---

## 2. 分阶段执行

分 4 个交付批次，每批结束全绿、可独立合并与回滚。

| 批次 | 阶段 | 目标 | 风险 |
|---|---|---|---|
| A | 0–2 | 护栏 + 基础层 + 配置存储 | 低 |
| B | 3–4 | 解循环 + 单一入口 | **高** |
| C | 5–8 | 领域子包门面化 | 中 |
| D | 9–10 | api 收敛 + 删 shim | 中 |

---

### Stage 0：架构护栏（不改任何行为）

**为什么先做**：后面 10 个阶段每个都要重复回答"这层能不能 import 那层"。把判断写成可执行断言，编程智能体就不必每步靠人眼确认，且能立刻发现自己引入的反向依赖。

1. 新建 `tools/check_architecture.py`：
   - 用 `ast` 遍历 `core/`、`api/`、`provider/`，构建模块级 import 图
   - 按 §1.1 的层序表断言"下层不得 import 上层"
   - 断言 `core.common` 零内部依赖
   - 断言各子包 `__init__.py` 的 `__all__` 长度 ≤ §1.2 预算
   - 断言禁止边：`core.* → provider.tools`、`core.* → api`、`provider.tools` 顶层 `→ core.ingest` / `core.service`
   - 输出与 `--fix-report` 无关，只打印违规清单并以非 0 退出
2. 新建 `tests/test_architecture.py`：调用上述检查器的核心函数，断言违规数为 0（阶段 0 先对**当前已满足的部分**断言，未满足的规则以 `xfail` 标注并写明对应阶段号）
3. 记录基线：`pytest tests/ -q` → 374 passed / 5 skipped；把该数字写进本文件 §5

**验收**：`python tools/check_architecture.py` 可运行并输出当前违规清单；`pytest tests/ -q` 仍是 374 passed。

**回滚点**：仅新增两个文件，`git revert` 即可。

---

### Stage 1：`core/common/` 基础层，消灭 30+ 处重复

1. 建 `core/common/{__init__,time,jsonio,text,fs,sql}.py`，按下表搬移并**统一为唯一实现**：

| 新位置 | 吸收的重复实现 |
|---|---|
| `common/time.now_iso` | `graph_organizer`:13, `ingestor/_utils`:11, `jobs`:6, `knowledge_support`:24, `portable_store`:17, `search_cache`:13, `provider/tools/graph_tools`, `update/updater`（**8 处**） |
| `common/time.elapsed_ms` | `ingestor/_utils`, `knowledge_support`, `rag_support`, `services/retrieval`（4 处） |
| `common/jsonio.write_json_atomic` | `db`, `ingestor/_utils`, `knowledge_support`, `portable_store`, `update/updater`（5 处） |
| `common/jsonio.write_bytes_atomic` | `knowledge_support`, `source_sync`, `update/updater`（3 处） |
| `common/text.required_text` | `graph_organizer`, `graph_visualization`, `ingestor/_utils`, `source_sync`, `provider/tools/delete_tools`, `provider/tools/graph_tools`（6 处） |
| `common/text.optional_text` | `graph_draft`, `portable_store`, `query_planner`（3 处） |
| `common/text.escape_like` | `knowledge_documents`, `provider/tools/delete_tools`, `provider/tools/graph_tools`（3 处） |
| `common/text.unique_texts` | `graph_draft`, `graph_organizer`, `ingestor/_utils` 等 |
| `common/text.parse_json_list` | `knowledge_support`, `services/graph`（逐字节相同） |
| `common/sql.{fetch_one,fetch_all,row_dict,transaction}` | `provider/tools/delete_tools` ↔ `graph_tools`（两组逐字节相同） |
| `common/fs.{validated_path,is_within,sha256_of}` | `ingestor/_utils._validated_path`、`rebuilder._is_within`、`source_sync._contained_markdown_path` |

2. 保留旧位置的同名私有函数作为**一层薄 wrapper**（`def _now_iso(): return now_iso()`），本阶段不删调用点。这样单次提交即可全绿。
3. 下一阶段（Stage 8 收尾）再删除这些 wrapper。

**硬约束**：`core/common/*` 只允许 import 标准库。`provider.tools.*` 可以 import `core.common.*`（单方向，安全）。

**验收**：
- `pytest tests/ -q` → 374 passed
- `tools/check_architecture.py` 的 "common 零内部依赖" 规则通过
- 逐字节重复函数对从 15 组降到 ≤4 组（`tests/test_architecture.py` 中加入该计数断言）

**回滚点**：新增文件 + wrapper，不改变任何行为。

---

### Stage 2：`core/settings/` + `core/storage/`

**关键约束**：`core/config.py`、`core/db.py` 是**模块**；新目标是**包**。Python 不允许同名共存，因此新包一律用**不同名**（`settings`/`storage`），旧模块降级为 shim。这是本阶段唯一容易出错的点。

1. `core/config.py`（481 行 / 9 类 / 10 函数）→ `core/settings/`：
   - `schema.py`：`AppConfig`、`KemoConfig`、`ModelSelectionConfig`、`VectorSearchConfig`、`QueryPlanningConfig`、`PortableStoreConfig`、`WebConfig`、`EntityExtractionConfig`
   - `defaults.py`：`default_config_dict`、`write_default_config`
   - `loader.py`：`load_config`、`write_config`、`_validate_with_fallback`、`_deep_merge`、`_migrate_legacy_provider_config`、`_get_nested`、`_set_nested`、`_resolve_runtime_path`、`DEFAULT_CONFIG_PATH`
   - `errors.py`：`ConfigLoadError`
   - `__init__.py`：导出 `AppConfig`、`KemoConfig`、`ConfigLoadError`、`DEFAULT_CONFIG_PATH`、`load_config`、`write_config`、`default_config_dict`、`write_default_config`
2. `core/db.py`（786 行 / 26 函数）→ `core/storage/`：
   - `paths.py`：`DatabasePaths`、`get_database_paths`
   - `schema.py`：`initialize_databases`、`_initialize_sqlite`、`_ensure_source_origin_columns`、`_ensure_source_external_columns`、`_ensure_rag_vector_space_column`、`_ensure_rag_chunk_hierarchy_columns` 等 6 个
   - `connections.py`：`connect_sources`、`connect_graph`、`connect_rag`、`initialize_search_cache`、`connect_search_cache`
   - `meta.py`：`default_graph_meta`、`read_graph_meta`、`write_graph_meta`、`default_rag_meta`、`read_rag_meta`、`write_rag_meta`
   - `locks.py`：并入 `core/locks.py`（19 行）
   - `portable/`：并入 `core/portable_store.py`（564 行 / 22 符号）→ `storage/portable/{__init__,manifest,paths,store,federation}.py`
3. `core/config.py`、`core/db.py`、`core/locks.py`、`core/portable_store.py` 改为 shim：
   ```python
   """DEPRECATED: 迁移至 core.settings，Stage 10 删除。"""
   from .settings import *  # noqa: F401,F403
   from .settings import __all__  # noqa: F401
   ```
4. `core/__init__.py` 的 re-export 改为指向新包（保持 `from core import AppConfig` 等 8 处旧用法可用）。

**验收**：
- `pytest tests/ -q` → 374 passed（33 处 `from core.config import ...`、15 处 `from core.db import ...` **零改动**）
- `python -c "import api"` 正常
- `tools/check_architecture.py` 层序通过

**回滚点**：shim 保证任何时刻可回退；本阶段不动调用方。

---

### Stage 3：解除 `core.ingest` ↔ `provider.tools` 循环

**这是唯一必须修改调用方的阶段，也是「低耦合」要求的落地。**

1. **文档转换实现下沉**
   - `provider/tools/document_tools.py` 的 15 个转换函数（`convert_pdf`/`convert_docx`/`convert_html`/`convert_txt`/`convert_rst`/`convert_csv`/`convert_spreadsheet`/`convert_pptx`/`convert_epub`/`convert_rtf`/`convert_eml`/`convert_data`/`import_document` + 3 个校验函数）下沉到 `core/documents/conversion.py`
   - `provider/tools/document_tools.py` 保留**同名同签名薄包装**（agent 工具接口不能变）：
     ```python
     from core.documents.conversion import convert_pdf, convert_docx, ...  # noqa: F401
     ```
   - `core/knowledge_support.py` 改为 `from .documents.conversion import ...`，**不再 import `provider.tools`**
   - 依赖方向修正为：`provider.tools → core.documents`（单向）
2. **`delete_tools` 的 `Ingestor` 改懒加载**
   - `provider/tools/delete_tools.py:19` 的 `from core.ingestor import Ingestor` 移到使用它的函数体内（`delete_document()`），并加注释说明原因
3. **`_graph_build` 反向边改注入**
   - `core/ingestor/_graph_build.py:176` 的 `from provider.tools import get_graph_tools, get_tool_schemas` 改为：`Ingestor.__init__` 接受可选 `tool_provider: ToolProvider | None = None`；`_graph_build` 只调用 `self._tool_provider.graph_tools()`，`None` 时抛出明确错误
   - `core/ingest/__init__` 门面**不** import `provider.tools`；由 `core/service`（L6，允许依赖 provider）在构造 `Ingestor` 时注入
4. 检查 `provider/tools/graph_tools.py` 与 `delete_tools.py` 的 11 个重复 helper（`_fetch_one`/`_fetch_all`/`_row_dict`/`_escape_like`/`_transaction`/`_required_text`/`_resolve_external_dir`/`_open_sqlite`/`_try_data_dir`/`_require_data_dir`/`_has_table`）：Stage 1 已建 `core/common/sql.py`，此处直接改为 import，删除本地副本。

**验收**：
- `tools/check_architecture.py` 断言通过：`core.* → provider.tools` 违规数为 **0**；`provider.tools` 模块顶层 → `core.ingest` 违规数为 **0**
- `pytest tests/ -q` → 374 passed
- `python -c "import provider.tools; import core.ingest"` 顺序无关，均成功

**回滚点**：本阶段改动集中在 3 个文件的 import 位置 + 1 个构造参数，可按文件回滚。

---

### Stage 4：`core/service/` 收敛为真正的单一入口（核心）

**目标**：把「门面 + 4 个壳 + 4 个 Mixin + 双向弹球」压成「门面 + 4 个域对象（真实现）」，调用最深两层。

1. 建 `core/service/`：
   - `context.py`：`ServiceContext` —— 把现有 `services/protocols.py` 的 `ServiceOwner` Protocol 实体化为具体类，持有 `settings`、`paths`、`data_dir`、`external_dir`、`logger`、`get_knowledge_base_lock`，并提供 `require_initialized()`、`require_available(*targets)`、`log_event(...)`、`new_graph_engine()`、`new_rag_engine()`、`new_hybrid_engine()`、`chat()`、`tool_provider`
   - `documents.py`：`DocumentService(Context)` —— 吸收 `knowledge_documents.py`（681 行实现）+ `services/document.py`（112 行转发壳）的**并集**
   - `graph.py`：`GraphService(Context)` —— 吸收 `knowledge_graph.py`（682 行）+ `services/graph.py`（447 行）。**重点：消除 `_get_node_impl` ↔ `get_node` 的来回转发，只保留一份实现**
   - `retrieval.py`：`RetrievalService(Context)` —— 吸收 `knowledge_retrieval.py`（153 行）+ `services/retrieval.py`（393 行真实逻辑）。**删除与 `knowledge_support` 逐字节重复的 `_build_answer_context`/`_dict_items`/`_clip_context_text`/`_elapsed_ms`，统一走 `common` 或本模块唯一副本**
   - `maintenance.py`：`MaintenanceService(Context)` —— 吸收 `knowledge_status.py`（203 行）+ `services/maintenance.py`（8 个转发方法）
   - `errors.py`：从 `knowledge_models.py` 迁入全部异常类与 `SUPPORTED_IMPORT_SUFFIXES` / `MAX_IMPORT_BYTES` / `CONFIG_API_KEY_MASK` / `_ImportSnapshot`
   - `facade.py`：`KnowledgeBaseService` —— `__init__` 建 `ServiceContext` + 4 个域对象，把域对象挂为 `self.documents` / `self.graph` / `self.retrieval` / `self.maintenance`；保留现有 **37 个公开方法**（`__init__` 之外的 37 个，逐个对照原 `core/knowledge_base.py` 列表核对，不得增删），每个函数体 ≤3 行
   - `__init__.py`：门面，导出 `KnowledgeBaseService`、全部异常类、`MAX_IMPORT_BYTES`、`SUPPORTED_IMPORT_SUFFIXES`（即当前 `core/knowledge_base.py` 的全部导出面）
2. 删除：`core/services/`（整包）、`core/knowledge_documents.py`、`core/knowledge_graph.py`、`core/knowledge_retrieval.py`、`core/knowledge_status.py`、`core/knowledge_models.py`
3. `core/knowledge_base.py` → shim：`from .service import *`
4. 改写 `tests/test_knowledge_base_services.py`：原测试断言的是「域对象暴露分开的区域 + 委托到 `_xxx_impl`」，该契约已不存在。改为断言**新契约**：
   - `service.documents.list_documents()` 与 `service.list_documents()` 返回一致（用 `tmp_path` 建真库）
   - 四个域对象均能独立完成一次读操作
   - `service.graph.delete_relation()` 不影响 `service.documents` 的返回

**绝对不可变**（否则 374 个测试必挂）：
- `KnowledgeBaseService` 的 38 个公开方法**名称、参数名、默认值、返回结构逐字节不变**
- 异常类的**类名与继承关系**不变（`api/errors.py` 用 `except` 捕获它们）
- `MAX_IMPORT_BYTES` / `SUPPORTED_IMPORT_SUFFIXES` / `CONFIG_API_KEY_MASK` 的**值**不变

**验收**：
- `pytest tests/ -q` → 374 passed（`test_knowledge_base_services.py` 除外，已按新契约改写且断言等价）
- `tools/check_architecture.py` 新增断言：`core/service/facade.py` 每个公开方法 AST 函数体 ≤3 条语句
- 断言 `core/service/{documents,graph,retrieval,maintenance}.py` 中**不含** `_xxx_impl` 字样
- `api/` 与 `provider/` 对 `core` 的 import 只出现 `core.service`（经 shim 的 `core.knowledge_base` 等价放行）

**回滚点**：本阶段删除 5 个文件，**必须单独一个 commit**，且 commit message 注明回滚方式。

---

### Stage 5：`core/graph/`

| 原文件 | 行数 | 去向 |
|---|---|---|
| `graph_engine.py` | 723 | `graph/engine.py`（`GraphEngine` 316 行）+ `graph/matching.py`（`_node_match_score`、`_match_key`、`_is_meaningful_contains_match`、`_entities_from_query_plan`、`_load_node_keywords`） |
| `graph_organizer.py` | 912 | `graph/organizer.py`（`GraphOrganizer` 366 行）+ `graph/candidates.py`（`_find_candidates`、`_neighbor_signatures`、`_jaccard`） |
| `graph_draft.py` | 373 | `graph/draft.py` |
| `graph_search.py` | 69 | `graph/search.py` |
| `graph_visualization.py` | 580 | `graph/visualization.py` + `graph/pagination.py`（`_validate_pagination`、`_pagination`、`_require_revision`） |
| `entity_extractor.py` | 237 | `graph/entity.py` |

`graph/__init__.py` 门面导出 ≤12：`GraphEngine`、`GraphError`、`GraphQueryError`、`GraphIntegrityError`、`GraphOrganizer`、`GraphOrganizerError`、`GraphDraft`、`DraftEntity`、`DraftRelation`、`GraphDraftError`、`Entity`、`EntityExtractionError`。
旧 6 个文件改为 shim。

**验收**：`pytest tests/ -q` → 374 passed；`core.graph` 不 import `core.service`；`graph/` 内 fan-in 集中在 `draft` 与 `search`。

---

### Stage 6：`core/rag/` + `core/retrieval/`

**`RAGEngine` 是 33 方法 / 859 行的上帝类，必须拆，但公开方法一个都不能动。**

1. `rag_engine.py`（927）→ `rag/`
   - `engine.py`：`RAGEngine` 只留 query / search 编排（≤10 方法）；原 33 个方法**全部保留**，但内部实现搬到下面各模块后以一次调用委托
   - `index.py`：`FaissIndexManager` + `_validate_vector_ids` + `_as_float32_matrix`（原 `faiss_index.py` 281 行）
   - `vectors.py`：`_coerce_embedding_result`、`_validate_embedding_batch`、`_extract_vector_space_id`（与 `ingestor/_rag_build.py` 的重复副本统一到此处）
   - `fusion.py`：`_fuse_vector_hits`、`_merge_candidates`、`_collapse_candidates_by_family`、`_chunk_family_id`、`_candidate_context_content`
   - `lexical.py`：`_planned_lexical_match_score`、`_direct_lexical_match_score`、`_normalize_lexical_text`
   - `auxiliary.py`：`RAGAuxiliaryMixin`（515 行）→ `RAGAuxiliary` 普通类
   - `text.py`：原 `fulltext.py`（`compile_fulltext`）
   - `errors.py`：原 `rag_errors.py`
   - `query_planner.py`：原 `query_planner.py`
2. `core/retrieval/`
   - `hybrid.py`：原 `hybrid.py`
   - `query_progress.py`、`query_logging.py`
   - `cache/keys.py`：`normalize_query`、`search_config_hash`、`compute_state_hash`、`make_cache_key`、`cache_key_lock`、`_hash_query_rows`、`_feed_digest`
   - `cache/store.py`：`SearchCache`、`SearchCacheError`、`SearchCacheCleanupError`、`decode_cached_result`、`clear_search_cache_after_mutation`
3. 旧文件（`rag_engine.py`、`rag_support.py`、`rag_auxiliary.py`、`rag_errors.py`、`faiss_index.py`、`fulltext.py`、`query_planner.py`、`hybrid.py`、`query_progress.py`、`query_logging.py`、`search_cache.py`）→ shim

**验收**：`pytest tests/ -q` → 374 passed；`RAGEngine` 类体 ≤400 行；`core.rag` 不 import `core.service`。

**风险点**：`tests/test_phase2_phase3a.py` 等直接调 `RAGEngine` 的内部行为。**只搬实现、不改逻辑**，若出现断言失败必须回到原实现比对，不得放宽断言。

---

### Stage 7：`core/ingest/`

1. `ingestor/__init__.py`（718 行，`Ingestor` 32 方法 / 584 行）→ `ingest/pipeline.py`，`Ingestor` 只留编排（≤12 方法），其余分拆：
   - `ingest/scan.py` ← `ingestor/_scan.py`
   - `ingest/graph_build.py` ← `ingestor/_graph_build.py`（948 行）
   - `ingest/prompt.py` ← 从 `graph_build` 抽出 5 个 prompt 构造函数（`_build_graph_system_prompt`、`_build_graph_section_prompt`、`_graph_granularity_rule`、`_bounded_graph_draft_schema`、`_graph_extraction_budget`）
   - `ingest/rag_build.py` ← `ingestor/_rag_build.py`
   - `ingest/delete.py` ← `ingestor/_delete.py`
   - `ingest/file_map.py` ← `ingestor/_file_map.py`
   - `ingest/chunker/` ← 原 `chunker.py`（675 行）拆为 `rules.py`（`_pre_split_by_paragraphs`、`_semantic_blocks`、`_semantic_hierarchy_spans`、`_group_adjacent_semantic_spans`）与 `llm.py`（`llm_chunk`、`_llm_chunk_ranges`、`_llm_part_ranges`、`_chunking_prompt_digest`）
   - `ingest/progress.py` ← 原 `build_progress.py`
   - `ingest/errors.py`
2. 删除 `ingestor/_utils.py` 全部 15 个函数（Stage 1 已归入 `common/`；此处删除 wrapper）
3. `ingest/__init__.py` 门面导出 ≤10：`Ingestor`、`ingest`、`scan_sources`、`delete_document`、`IngestError`、`DocumentNotFoundError`、`SourceChangedDuringIngest`、`FileMapStore`、`FileMapError`、`ScanResult`
4. 旧 `core/ingestor/`、`core/chunker.py`、`core/build_progress.py` → shim

**验收**：`pytest tests/ -q` → 374 passed；`Ingestor` 类体 ≤400 行；`tests/test_path_safety.py` 对 `_delete_source_file`、`tests/test_graph_extraction_granularity.py` 对 `_sparsify_graph_relations` 的引用经 shim 仍可用。

---

### Stage 8：`core/documents/` + `core/maintenance/` + `core/observability/`

1. `documents/`：`library.py`(498)→`documents/library.py`；`document_preview.py`→`documents/preview.py`；`build_documents.py`→`documents/build.py`；`conversion.py`（Stage 3 已建）；`formats.py`（`SUPPORTED_IMPORT_SUFFIXES` 等）
2. `maintenance/`：`jobs.py`(478)→`maintenance/jobs.py`；`rebuilder.py`(585)→`maintenance/rebuild.py`；`scheduler.py`→`maintenance/scheduler.py`；`source_sync.py`(675)→`maintenance/source_sync.py`
3. `observability/`：`logger.py`、`log_viewer.py`、`terminal_logging.py`、`read_cache.py`
4. 各自 `__init__.py` 门面（导出 ≤8）
5. 旧文件 → shim

**验收**：`pytest tests/ -q` → 374 passed；`tools/check_architecture.py` 层序全部通过；`core` 根目录文件数从 41 降到 ≤5（仅剩 `__init__.py` 与 shim）。

---

### Stage 9：`api/` 收敛

1. `routes.py`（778 行 / 53 路由）→ `api/routes/` 包：
   - `system.py`：`/status`、`/config`×3、`/update`×3
   - `query.py`：`/query/*`×5、`/search/cache`×3
   - `documents.py`：`/documents/*`×6、`/upload`、`/import`、`/ingest`
   - `graph.py`：`/nodes/*`×3、`/relations/*`×2、`/graph`、`/graph/full`、`/graph/visualization/*`×3、`/graph/neighborhood/*`、`/graph/search`
   - `jobs.py`：`/jobs`×5、`/maintenance/*`×4
   - `stores.py`：`/stores/*`×6（吸收 `store_routes.py` 670 行）
   - `build.py`：吸收现有 `build_routes.py`
   - `library.py`：吸收现有 `library_routes.py`
   - `logs.py`：吸收现有 `log_routes.py`
   - `__init__.py`：聚合所有子 `router` 为单一 `router`，使 `from .routes import router` 不变
2. `schemas.py`（526 行 / 51 类）→ `api/schemas/` 包：`common.py`、`query.py`、`documents.py`、`graph.py`、`stores.py`、`maintenance.py`；旧 `schemas.py` → shim
3. `api/__init__.py` 末尾的 `app = create_app()`（模块级副作用）改为 PEP 562 惰性属性：
   ```python
   def __getattr__(name: str):
       if name == "app":
           global app
           app = create_app()
           return app
       raise AttributeError(name)
   ```
   改造前先 `grep -rn "api:app"` 与 `from api import app` 确认无直接依赖；若有，保留惰性属性并验证 `uvicorn api:app` 仍工作。

**验收**：
- `pytest tests/ -q` → 374 passed
- 扩展 `tests/test_api_surface.py`：断言 `app.routes` 的 **路径集合、方法集合、顺序** 与改造前完全一致（把改造前的路由表固化进测试）
- `api/routes/*.py` 每个 ≤250 行
- `python -c "import api"` 不再构造 app（用 `sys.modules` 检查无 `app` 属性即可）

---

### Stage 10：删除 shim 并收尾

1. 删除全部 shim 模块（`core/config.py`、`core/db.py`、`core/graph_engine.py` … 约 40 个）
2. `core/__init__.py` 成为唯一对外清单，导出 ≤24 个高频符号（`AppConfig`、`load_config`、`initialize_databases`、`get_database_paths`、`KnowledgeBaseService` + 全部异常类 + `GraphEngine`、`RAGEngine`、`HybridEngine`、`Ingestor`、`SearchCache`、`MaintenanceJobManager` …）
3. 同步更新 **测试文件的 import 行**（脚本辅助，机械替换），**断言内容一个字都不改**
4. 文档：重写 `core/services/README.md` → `core/service/README.md`（说明层序、门面预算、依赖方向）；更新 `api.md`、`README.md`、`README.en.md` 中的模块路径
5. `tools/check_architecture.py` 接入 CI（`.github/workflows/` 中与 pytest 同 job）

**验收**：
- `pytest tests/ -q` → 374 passed / 5 skipped（与基线一致）
- `python tools/check_architecture.py` → 0 违规
- `grep -rn "core\.\(graph_engine\|rag_engine\|search_cache\|knowledge_base\|portable_store\|db\|config\)\b" --include=*.py .` → 0 结果

---

## 3. 通用硬约束

1. **每个 Stage 一个独立 commit**，message 写清「改了什么 / 为什么 / 验收方式 / 回滚方式」。
2. **每个 Stage 结束必须 `pytest tests/ -q` 全绿**，失败数不得高于基线（0）。
3. **不修改任何数据库 schema**，不写数据迁移脚本。`data/` 下三个 SQLite 文件不动。
4. **不修改 HTTP 路由路径、请求体、响应结构**。`api.md` 是权威。
5. **不修改 CLI 参数**（`start.py`、`start_web.py`、`restart.py`、`update.py`、`convert.py`）。
6. **不删减任何公开方法、异常类、常量**。Stage 4 的 `KnowledgeBaseService` 37 个公开方法、`RAGEngine` 33 个方法、`Ingestor` 32 个方法，一个都不能少。
7. **不改 `config/` 下 5 份提示词与 `config/*.yaml` 的字段名**。
8. **不顺手"优化"业务逻辑**。本方案是纯结构重排。发现 bug 单独记录，不在本次修。
9. **shim 的 `__all__` 必须与源模块完全一致**，否则 `from core.config import X` 会静默失败。
10. **遇到无法解释的测试失败，停下来报告**，不得放宽断言或 `xfail` 掩盖（Stage 0 已有的 `xfail` 除外，且删除时须同步删标注）。

---

## 4. 命名与冲突规避（关键实现细节）

`config.py` / `db.py` 是模块，新目标是包，**Python 不允许同名共存**。因此：

| 旧模块（文件） | 新包（目录） | 冲突？ |
|---|---|---|
| `core/config.py` | `core/settings/` | 无 |
| `core/db.py` | `core/storage/` | 无 |
| `core/ingestor/`（已存在） | `core/ingest/` | **无**（`ingest` ≠ `ingestor`） |
| `core/services/`（已存在） | `core/service/` | **无**（`service` ≠ `services`） |
| `core/graph_engine.py` 等 | `core/graph/` | 无 |
| `core/rag_engine.py` 等 | `core/rag/` | 无 |

**禁止**在方案执行中创建 `core/config/`、`core/db/`、`core/graph_engine/` 这类与现有 `.py` 同名的目录。

---

## 5. 测试基线（唯一回归参照）

```
python -m pytest tests/ -q --no-header
→ 374 passed, 5 skipped, 1 warning, 2 subtests passed in 180.17s
```

- 5 个 skip 是既有 skip，**不得增加**
- 每个 Stage 结束必须复跑并与此比对
- Stage 4、6、7 因触及 `test_knowledge_base_services.py` / `RAGEngine` / `Ingestor` 内部，失败时必须先确认是「结构改动导致测试引用失效」还是「真实行为回归」——前者改 import，后者回滚

---

## 6. 收益量化

| 阶段 | 可验证指标 | 改造前 | 改造后 |
|---|---|---|---|
| 1 | 逐字节重复函数组 | 15 组 / 30+ 处 | ≤4 组 |
| 2 | `core` 根平铺模块 | 41 | 41（未动，shim） |
| 3 | `core → provider.tools` 违规边 | 3 处（1 处函数内绕行） | 0 |
| 4 | `delete_document` 定义处 | 6 | 2（门面 1 + 域对象 1） |
| 4 | `KnowledgeBaseService` 调用深度 | 3 层（sim → minin → service 弹球） | 2 层 |
| 4 | `_xxx_impl` 方法数 | 39 | 0 |
| 6 | `RAGEngine` 类体行数 | 859 | ≤400 |
| 7 | `Ingestor` 类体行数 | 584 | ≤400 |
| 8 | `core` 根目录 `.py` 文件 | 41 | ≤5 |
| 9 | `routes.py` 行数 / 路由数 | 778 / 53 | ≤250 / ≤15 |
| 10 | 旧扁平路径引用 | ~200 处 | 0 |

---

## 7. 明确不做的事

1. **不引入任何新的第三方依赖**（`rapidfuzz`、`ahocorasick_rs` 等属于 `REFACTOR-PLAN.md` 的性能议题，与本方案无关）。
2. **不拆分 `GraphEngine` / `GraphOrganizer` 的算法逻辑**。只把模块级私有函数归入 `graph/matching.py` 与 `graph/candidates.py`，类方法体不重排。
3. **不改 `provider/` 的内部结构**。只修正它对 `core` 的依赖方向（Stage 3），`provider/embedding.py`、`engine.py`、`rerank.py`、`model_catalog.py` 保持原样。
4. **不动 `markitdown/`、`core/sqlite_simple/`、`tools/vendor_simple.py`**。它们是 vendored 第三方代码。
5. **不动 `update/updater.py` 的内部逻辑**。仅通过 `common/` 复用 `_now_iso`/`_write_json_atomic`/`_write_bytes_atomic`。
6. **不重构 `web/frontend/`**。
7. **不追求一次做完**。批次 A→B→C→D 顺序执行，每批次可独立合并。若中途停止，已完成的批次仍是收益。

---

## 8. 完成度自检清单

编程智能体在提交前逐条确认：

- [x] `pytest tests/ -q` → 436 passed / 5 skipped / 0 failed；实际基线及新增护栏测试见 §9–10
- [x] `python tools/check_architecture.py` → 退出码 0，0 违规
- [x] `python -c "import api; import provider.tools; import core.ingest"` 成功
- [x] `start.py`、`start_web.py`、`restart.py`、`update.py`、`convert.py` 的 `--help` 全部正常
- [x] Stage 10 后 `core/` 根目录仅剩 `__init__.py`，共 1 个 `.py` 文件
- [x] 无 `core/config/`、`core/db/` 等与旧模块同名的目录
- [x] 迁移期 shim 已删除，无残留兼容导出
- [x] `core/service/facade.py` 每个公开方法 ≤3 条语句
- [x] `core/*/__init__.py` 的 `__all__` 长度符合 §9 校正后的实际契约预算
- [x] 无新增 `# type: ignore`、`# noqa`
- [x] 无 `xfail` 遗留

## 9. 执行校正（2026-11-14）

- 执行前实测：433 passed、5 skipped、2 subtests passed，耗时 173.60s；新增架构测试计入后续总数。
- 同名校验函数只有语义一致才归并，原有异常、Unicode 规范化与长度限制保持不变。
- 文档异常与格式常量归 documents，服务异常归 service，门面统一重新导出；避免文档层反向依赖服务层。
- 图谱工具实现与注册下沉到 graph，provider 继续导出相同工具函数；Ingestor 的默认工具模式保留，允许显式注入。
- 查询进度属于 observability；查询规划与文档切分供图谱及 RAG 共用，归入 documents，避免下层依赖 ingest。
- portable 的跨库服务装配与联合检索属于 service；storage 只负责清单、目录与持久化。
- 迁移测试 import 和 patch 目标随对应阶段更新，保留业务断言；服务委托测试改为真实库行为测试。
- api/routes 和 api/schemas 同名模块改包时直接替换；兼容导出由新包承担。
- 导出预算按实际外部契约校正，分组子门面公开必要的 API；根目录文件数指标在 Stage 10 删除兼容文件后验收。
- 每阶段全量测试通过后独立本地提交，不推送；既有后端改动先单独记录为基线，前端改动不纳入本次提交。


### Stage 10 执行校正

- 根 `core` 使用与 `api.app` 相同的 PEP 562 惰性导出，保留 24 符号预算。直接在根门面提前加载全部引擎会与 provider 的配置导入形成循环，因此根门面不提前装配服务；其余子包保持纯导入门面。
- 细分领域能力继续使用明确的领域模块路径，常用入口使用包门面；不为了包住已经独立的文档操作、格式转换、图谱工具而额外增加转发包。层序、旧路径、门面预算及副作用约束由架构检查执行。
- 原始契约逐项核对：KnowledgeBaseService 38 个方法签名、RAGEngine 33 个、Ingestor 32 个均保留；类体分别为 349、259、339 行。
- Stage 9 核对 110 条有序路由、95 个 OpenAPI 路径及完整 OpenAPI 定义一致；新增路由顺序与惰性应用测试后为 436 passed、5 skipped、2 subtests passed。

## 10. 最终验收（2026-11-14）

Stage 0–10 全部完成，每阶段在全量测试通过后独立本地提交，分支为 `kemo-preview`，未推送远程。执行前已有后端改动单独保存在基线提交中；既有前端改动与 `.env.example` 删除保留在工作区，不纳入重构提交。

| 验收项 | 最终结果 |
| --- | --- |
| 全量测试 | 436 passed、5 skipped、2 subtests passed，183.76s；0 failed |
| 架构检查 | Stage 10，0 violation(s) |
| 原始方法签名 | KnowledgeBaseService 38 / RAGEngine 33 / Ingestor 32，全保留 |
| 类体规模 | KnowledgeBaseService 349 / RAGEngine 259 / Ingestor 339 行 |
| HTTP 合约 | 110 条有序路由、95 个 OpenAPI 路径及完整定义与迁移前快照一致 |
| 启动与导入 | 五个 CLI 帮助入口、`api:app` 惰性入口、provider 先导入与 core 先导入均通过 |
| 扁平模块 | `core/` 根目录仅 1 个 `.py` 文件；受 Git 管理的 Python 代码无旧模块导入 |
| 语法检查 | core / api / provider / update 及启动脚本 compileall 通过 |
| CI | 架构检查与 pytest 在同一 backend job 执行 |

实际执行基线为 433 passed，新增 1 项架构检查与 2 项 API 契约测试后为 436 passed；跳过数保持 5。保留既有 Starlette/httpx 弃用提示，未降低业务断言或增加 xfail。

删除兼容模块后，历史算法比对加载器只替换历史源码的导入位置，固定 Git 版本、算法与等价断言均不变；42 项节点匹配、候选排序回归通过。公共 SHA-256 文件读取使用分块标准库实现，兼容项目声明的 Python 3.10，避免使用 Python 3.11 才提供的 `hashlib.file_digest`。

业务实现、HTTP/CLI 合约、配置字段与数据库 schema 保持原有语义，未创建数据迁移。模块层序、门面预算及回滚方式见 `core/service/README.md`。
