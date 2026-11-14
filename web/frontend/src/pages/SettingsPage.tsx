import {
  CheckCircle2,
  CircleHelp,
  Cpu,
  Database,
  Download,
  History,
  KeyRound,
  Network,
  Power,
  RefreshCw,
  RotateCcw,
  Save,
  Scissors,
  ServerCog,
  Tag,
  TextSearch,
  Workflow,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/api";
import { ErrorNotice, InfoNotice, LoadingState } from "../components/Feedback";
import { ModelPicker } from "../components/ModelPicker";
import { PageIntro } from "../components/PageIntro";
import { ThemedSelect } from "../components/ThemedSelect";
import type { ConfigData, ModelCatalogData, ModelTask, UpdateStatusData } from "../types/api";

type FieldKind = "boolean" | "model" | "number" | "password" | "select" | "text" | "time";

type FieldOption = {
  label: string;
  value: string;
};

type ConfigField = {
  path: string;
  label: string;
  description: string;
  kind: FieldKind;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  options?: FieldOption[];
  optional?: boolean;
  readOnly?: boolean;
  placeholder?: string;
  modelTask?: ModelTask;
};

const BOOLEAN_OPTIONS = [
  { value: "true", label: "启用" },
  { value: "false", label: "停用" },
];

type ConfigSection = {
  title: string;
  description?: string;
  fields: ConfigField[];
};

type ConfigGroup = {
  id: string;
  title: string;
  description: string;
  icon: typeof Network;
  sections: ConfigSection[];
};

const groups: ConfigGroup[] = [
  {
    id: "gateway",
    title: "网关连接",
    description: "地址、密钥与超时",
    icon: KeyRound,
    sections: [
      {
        title: "连接设置",
        fields: [
          { path: "kemo.base_url", label: "网关 Base URL", description: "Kemo Gateway 的根地址。", kind: "text" },
          { path: "kemo.request_timeout", label: "请求超时", description: "单次 Kemo 模型请求的最长等待时间。", kind: "number", min: 1, step: 1, suffix: "秒" },
        ],
      },
      {
        title: "访问认证",
        fields: [
          { path: "kemo.api_key", label: "API Key", description: "配置网关访问密钥；已保存的密钥以掩码显示。", kind: "password", optional: true, placeholder: "输入 API Key" },
          { path: "kemo.protocol_version", label: "协议版本", description: "当前网关严格使用 Kemo 2.0 协议，只读显示。", kind: "text", readOnly: true },
        ],
      },
    ],
  },
  {
    id: "models",
    title: "模型配置",
    description: "模型、维度与批量",
    icon: Cpu,
    sections: [
      {
        title: "模型选择",
        description: "保存网关后自动获取模型，也可手动填写。",
        fields: [
          { path: "models.llm", label: "LLM 模型", description: "用于实体抽取、图谱构建和总结生成。", kind: "model", modelTask: "llm" },
          { path: "models.embedding", label: "Embedding 模型", description: "用于生成文档和查询向量。", kind: "model", modelTask: "embedding" },
          { path: "models.rerank", label: "Rerank 模型", description: "用于对向量召回候选进行相关性重排。", kind: "model", modelTask: "rerank" },
        ],
      },
      {
        title: "向量生成",
        description: "修改向量维度后需重建索引。",
        fields: [
          { path: "models.embedding_dimensions", label: "向量维度", description: "模型返回的向量长度；修改后需要重建向量索引。", kind: "number", min: 1, step: 1, suffix: "维" },
          { path: "embedding_batch_size", label: "Embedding 单批数量", description: "每次发送给网关的最大切片数；实际值不会超过模型能力上限。", kind: "number", min: 1, max: 256, step: 1, suffix: "条" },
        ],
      },
    ],
  },
  {
    id: "chunking",
    title: "文档切片",
    description: "切分方式与粒度",
    icon: Scissors,
    sections: [
      {
        title: "切分方式",
        fields: [
          { path: "chunking_mode", label: "切片模式", description: "语义分层模式以 LLM 边界为叶子，再建立中、大粒度父级。", kind: "select", options: [{ label: "LLM 语义分层（推荐）", value: "semantic_hierarchical" }, { label: "LLM 单层语义切片", value: "llm" }, { label: "机械分层切片", value: "hierarchical" }, { label: "固定切片", value: "fixed" }] },
          { path: "chunking_llm_max_input_chars", label: "LLM 切分输入上限", description: "长文档会先按自然边界预切，再逐段请求 LLM 选择语义边界。", kind: "number", min: 2000, max: 50000, step: 100, suffix: "字符" },
        ],
      },
      {
        title: "片段大小与重叠",
        description: "分层切片需满足：小 < 中 < 大。",
        fields: [
          { path: "chunk_small_size", label: "小粒度切片", description: "负责关键词、实体和精确片段召回。", kind: "number", min: 64, max: 2048, step: 1, suffix: "tokens" },
          { path: "chunk_size", label: "中粒度切片", description: "负责段落级语义与命中后的主要上下文。", kind: "number", min: 128, max: 4096, step: 1, suffix: "tokens" },
          { path: "chunk_large_size", label: "大粒度切片", description: "负责章节主题召回和较完整的上下文。", kind: "number", min: 256, max: 8192, step: 1, suffix: "tokens" },
          { path: "chunk_overlap", label: "中粒度重叠", description: "中粒度的重叠 token 数；分层模式会按相同比例计算其他层级。", kind: "number", min: 0, max: 512, step: 1, suffix: "tokens" },
        ],
      },
    ],
  },
  {
    id: "graph-build",
    title: "图谱构建",
    description: "实体、关系与摘要",
    icon: Workflow,
    sections: [
      {
        title: "实体抽取",
        fields: [
          { path: "entity_extraction.method", label: "实体抽取方式", description: "LLM 模式使用模型理解语义；规则模式适合轻量处理。", kind: "select", options: [{ label: "LLM 智能抽取", value: "llm" }, { label: "规则抽取", value: "rule" }] },
          { path: "entity_extraction.max_entities", label: "单次最大实体数", description: "每次查询或抽取最多保留的实体数量。", kind: "number", min: 1, max: 100, step: 1, suffix: "个" },
        ],
      },
      {
        title: "构建策略",
        fields: [
          { path: "graph_build_mode", label: "图谱构建模式", description: "自动模式优先使用结构化输出；工具模式适合需要逐步检索和写入的网关。", kind: "select", options: [{ label: "自动（推荐）", value: "auto" }, { label: "结构化输出", value: "structured" }, { label: "工具调用", value: "tools" }] },
          { path: "graph_extract_granularity", label: "图谱抽取颗粒度", description: "默认采用粗粒度，只保留稳定主题和关键关系；细粒度会显著增加节点与关系数量。", kind: "select", options: [{ label: "粗粒度（推荐，较少节点）", value: "large" }, { label: "标准", value: "medium" }, { label: "细粒度（更多节点）", value: "small" }] },
          { path: "graph_extract_chunk_size", label: "图谱基准分段大小", description: "颗粒度档位以此字符数为基准；调整后需要重新整理文档才能生效。", kind: "number", min: 2000, max: 100000, step: 500, suffix: "字符" },
          { path: "graph_extract_concurrency", label: "并行抽取数量", description: "结构化模式同时处理的文档分段数量；工具模式保持串行事务写入。", kind: "number", min: 1, max: 8, step: 1, suffix: "段" },
          { path: "graph_tool_max_iterations", label: "工具调用最大轮次", description: "图谱构建智能体在单篇文档中的最大工具循环次数。", kind: "number", min: 1, max: 200, step: 1, suffix: "轮" },
        ],
      },
      {
        title: "节点群摘要",
        fields: [
          { path: "summary_trigger_file_count", label: "摘要触发文件数", description: "累计达到此数量后触发群组摘要生成。", kind: "number", min: 1, step: 1, suffix: "篇" },
          { path: "summary_trigger_time", label: "每日摘要时间", description: "后台调度器每日运行群组摘要任务的本地时间。", kind: "time" },
        ],
      },
    ],
  },
  {
    id: "query-planning",
    title: "查询规划",
    description: "改写、拆分与融合",
    icon: TextSearch,
    sections: [
      {
        title: "问题理解与扩展",
        fields: [
          { path: "query_planning.mode", label: "查询规划模式", description: "自动模式优先使用 LLM，失败时保留原始查询安全降级。", kind: "select", options: [{ label: "自动（推荐）", value: "auto" }, { label: "始终使用 LLM", value: "llm" }, { label: "仅规则规范化", value: "rule" }, { label: "关闭扩展", value: "off" }] },
          { path: "query_planning.max_rewrites", label: "最大改写数量", description: "最多生成的同义表达、别名和相关概念数量。", kind: "number", min: 0, max: 12, step: 1, suffix: "条" },
          { path: "query_planning.max_subqueries", label: "最大子问题数", description: "复杂多意图问题最多拆分出的独立检索问题数量。", kind: "number", min: 0, max: 8, step: 1, suffix: "条" },
          { path: "query_planning.max_total_queries", label: "查询总数上限", description: "包含原始问题在内，一次检索允许使用的查询向量总数。", kind: "number", min: 1, max: 16, step: 1, suffix: "条" },
          { path: "query_planning.semantic_drift_threshold", label: "语义漂移阈值", description: "扩展词与原始问题向量低于此相似度时会被丢弃。", kind: "number", min: -1, max: 1, step: 0.05 },
        ],
      },
      {
        title: "多路候选控制",
        fields: [
          { path: "query_planning.candidate_pool_size", label: "候选池大小", description: "多路召回融合后送入 Rerank 的最大候选规模。", kind: "number", min: 5, max: 200, step: 5, suffix: "条" },
          { path: "query_planning.rrf_k", label: "RRF 平滑常数", description: "控制多路排名融合的平滑程度，普通用户建议保持默认。", kind: "number", min: 1, max: 1000, step: 1 },
          { path: "query_planning.low_confidence_rescue_count", label: "低置信度兜底数", description: "严格阈值无命中时最多保留的候选数量。", kind: "number", min: 0, max: 10, step: 1, suffix: "条" },
        ],
      },
    ],
  },
  {
    id: "retrieval",
    title: "召回与重排",
    description: "阈值、排序与增强",
    icon: Database,
    sections: [
      {
        title: "文档片段检索",
        fields: [
          { path: "default_top_k", label: "默认召回数量", description: "向量检索默认取回的候选片段数量。", kind: "number", min: 1, step: 1, suffix: "条" },
          { path: "rag_similarity_threshold", label: "相似度阈值", description: "低于该分数的向量候选不会进入结果集。", kind: "number", min: 0, max: 1, step: 0.05 },
          { path: "rerank_top_n", label: "重排保留数量", description: "Rerank 后最终保留的候选片段数量。", kind: "number", min: 1, step: 1, suffix: "条" },
        ],
      },
      {
        title: "混合与全局检索",
        fields: [
          { path: "hybrid_enhancement_factor", label: "混合增强系数", description: "图谱锚定 chunk 的 FAISS 分数增强倍率。", kind: "number", min: 0, step: 0.1, suffix: "倍" },
          { path: "vector_search.entity_weight", label: "实体向量权重", description: "混合检索中实体摘要向量分数的乘数。", kind: "number", min: 0, max: 2, step: 0.1, suffix: "倍" },
          { path: "vector_search.entity_top_k", label: "实体召回数量", description: "混合检索最多返回的实体语义结果数。", kind: "number", min: 1, max: 100, step: 1, suffix: "个" },
          { path: "vector_search.community_weight", label: "群组向量权重", description: "混合检索中节点群总结向量分数的乘数。", kind: "number", min: 0, max: 2, step: 0.1, suffix: "倍" },
          { path: "vector_search.community_top_k", label: "群组召回数量", description: "混合与全局检索默认返回的群组语义结果数。", kind: "number", min: 1, max: 100, step: 1, suffix: "个" },
        ],
      },
    ],
  },
  {
    id: "graph-view",
    title: "图谱查询与展示",
    description: "遍历范围与标签",
    icon: Network,
    sections: [
      {
        title: "图谱查询",
        fields: [
          { path: "max_query_depth", label: "最大查询深度", description: "图谱查询允许扩展的最大 BFS 层数。", kind: "number", min: 1, max: 10, step: 1, suffix: "层" },
          { path: "default_confidence", label: "默认置信度", description: "实体匹配与图谱检索的默认最低置信度。", kind: "number", min: 0, max: 1, step: 0.05 },
        ],
      },
      {
        title: "网页图谱展示",
        fields: [
          { path: "web_node_load_depth", label: "网页节点加载深度", description: "打开图谱页面时默认加载的邻居层数。", kind: "number", min: 0, max: 10, step: 1, suffix: "层" },
          { path: "web_node_label_threshold", label: "节点标签阈值", description: "达到此引用次数的节点优先显示标签。", kind: "number", min: 0, step: 1, suffix: "次引用" },
        ],
      },
    ],
  },
  {
    id: "cache",
    title: "搜索缓存",
    description: "复用、容量与清理",
    icon: History,
    sections: [
      {
        title: "缓存策略",
        description: "清理缓存不影响知识文件。",
        fields: [
          { path: "search_cache_enabled", label: "启用搜索缓存", description: "CLI、API 与网页端共享同一份服务端搜索缓存。", kind: "boolean" },
          { path: "search_cache_max_entries", label: "缓存记录上限", description: "超过上限时优先裁剪最久未访问的搜索结果。", kind: "number", min: 100, max: 100000, step: 100, suffix: "条" },
          { path: "search_cache_max_bytes", label: "缓存空间上限", description: "缓存结果正文允许占用的最大总字节数。", kind: "number", min: 1048576, step: 1048576, suffix: "字节" },
        ],
      },
    ],
  },
  {
    id: "system",
    title: "系统维护",
    description: "存储、日志与服务",
    icon: ServerCog,
    sections: [
      {
        title: "文档容量",
        fields: [
          { path: "max_documents", label: "最大文档数", description: "知识库允许管理的最大文档数量。", kind: "number", min: 1, step: 1, suffix: "篇" },
        ],
      },
      {
        title: "运行路径与监听地址",
        description: "相对路径以项目目录为准；修改后需重启服务。",
        fields: [
          { path: "data_dir", label: "索引与数据目录", description: "保存数据库、索引及任务状态。修改后请重启服务。", kind: "text" },
          { path: "external_dir", label: "预处理文档目录", description: "保存用于构建的 Markdown 映射文件。修改后请重启服务。", kind: "text" },
          { path: "web.host", label: "监听地址", description: "Web 服务绑定的主机地址。", kind: "text" },
          { path: "web.port", label: "监听端口", description: "Web 服务端口，范围 1–65535。", kind: "number", min: 1, max: 65535, step: 1 },
        ],
      },
      {
        title: "运行日志",
        fields: [
          { path: "log_dir", label: "日志目录", description: "相对于项目根目录的日志输出位置。", kind: "text" },
          { path: "log_level", label: "日志级别", description: "只记录此级别及以上的运行信息。", kind: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"].map((value) => ({ label: value, value })) },
        ],
      },
    ],
  },
];

function getConfigValue(config: ConfigData, path: string): unknown {
  return path.split(".").reduce<unknown>((value, key) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
    return (value as Record<string, unknown>)[key];
  }, config);
}

function setConfigValue(config: ConfigData, path: string, value: unknown): ConfigData {
  const keys = path.split(".");
  const next: ConfigData = { ...config };
  let cursor: Record<string, unknown> = next;

  keys.forEach((key, index) => {
    if (index === keys.length - 1) {
      cursor[key] = value;
      return;
    }
    const existing = cursor[key];
    const child = existing && typeof existing === "object" && !Array.isArray(existing)
      ? { ...(existing as Record<string, unknown>) }
      : {};
    cursor[key] = child;
    cursor = child;
  });
  return next;
}

function validateConfig(config: ConfigData): string | null {
  for (const group of groups) {
    for (const section of group.sections) {
      for (const field of section.fields) {
        const value = getConfigValue(config, field.path);
        if (field.readOnly) continue;
        if (field.optional && (value === undefined || value === null || value === "")) continue;
        if (field.kind === "boolean") {
          if (typeof value !== "boolean") return `${field.label}必须是布尔值`;
        } else if (field.kind === "number") {
          if (typeof value !== "number" || !Number.isFinite(value)) return `${field.label}必须是有效数字`;
          if (field.min !== undefined && value < field.min) return `${field.label}不能小于 ${field.min}`;
          if (field.max !== undefined && value > field.max) return `${field.label}不能大于 ${field.max}`;
        } else if (typeof value !== "string" || !value.trim()) {
          return `${field.label}不能为空`;
        } else if (field.options && !field.options.some((option) => option.value === value)) {
          return `${field.label}的选项无效`;
        }
      }
    }
  }

  const chunkSize = getConfigValue(config, "chunk_size");
  const chunkOverlap = getConfigValue(config, "chunk_overlap");
  if (typeof chunkSize === "number" && typeof chunkOverlap === "number" && chunkOverlap >= chunkSize) {
    return "切片重叠必须小于切片大小";
  }
  if (["hierarchical", "semantic_hierarchical"].includes(String(getConfigValue(config, "chunking_mode")))) {
    const small = getConfigValue(config, "chunk_small_size");
    const large = getConfigValue(config, "chunk_large_size");
    if (
      typeof small === "number"
      && typeof chunkSize === "number"
      && typeof large === "number"
      && !(small < chunkSize && chunkSize < large)
    ) {
      return "分层切片必须满足：小粒度 < 中粒度 < 大粒度";
    }
  }
  return null;
}

export function SettingsPage() {
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [savedConfig, setSavedConfig] = useState<ConfigData | null>(null);
  const [modelCatalog, setModelCatalog] = useState<ModelCatalogData | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelsRevision, setModelsRevision] = useState(0);
  const [activeGroupId, setActiveGroupId] = useState(groups[0].id);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [clearingCache, setClearingCache] = useState<"all" | "stale" | null>(null);
  const [restartingService, setRestartingService] = useState(false);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusData | null>(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const panelRef = useRef<HTMLElement>(null);

  const activeGroup = groups.find((group) => group.id === activeGroupId) ?? groups[0];
  const dirty = useMemo(
    () => Boolean(config && savedConfig && JSON.stringify(config) !== JSON.stringify(savedConfig)),
    [config, savedConfig],
  );
  const gatewayDirty = Boolean(config && savedConfig
    && JSON.stringify(config.kemo) !== JSON.stringify(savedConfig.kemo));
  const catalogError = gatewayDirty ? "网关连接有未保存的修改，请保存配置后更新模型列表。" : modelsError;

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    setNotice(null);
    setSaved(false);
    try {
      const data = await api.getConfig();
      setConfig(data);
      setSavedConfig(data);
    } catch (caught) {
      setConfig(null);
      setSavedConfig(null);
      setError(caught instanceof Error ? caught.message : "无法读取配置");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  useEffect(() => {
    if (!savedConfig) return;
    const controller = new AbortController();
    setModelCatalog(null);
    setModelsError(null);
    if (getConfigValue(savedConfig, "kemo.api_key_source") === "none") {
      setModelsLoading(false);
      setModelsError("请先在「网关连接」中配置并保存 API Key。");
      return;
    }
    setModelsLoading(true);
    void api.getModels(controller.signal).then((data) => {
      if (!controller.signal.aborted) setModelCatalog(data);
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setModelsError(caught instanceof Error ? caught.message : "无法读取上游模型列表");
    }).finally(() => {
      if (!controller.signal.aborted) setModelsLoading(false);
    });
    return () => controller.abort();
  }, [savedConfig, modelsRevision]);


  useEffect(() => {
    panelRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [activeGroupId]);

  const updateField = (field: ConfigField, rawValue: string) => {
    if (!config || field.readOnly) return;
    const nextValue = field.kind === "number" && rawValue !== ""
      ? Number(rawValue)
      : field.kind === "boolean"
        ? rawValue === "true"
        : rawValue;
    setConfig(setConfigValue(config, field.path, nextValue));
    setSaved(false);
    setNotice(null);
    setError(null);
  };

  const save = async () => {
    if (!config) return;
    const validationError = validateConfig(config);
    if (validationError) {
      setError(validationError);
      return;
    }

    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const responseConfig = await api.saveConfig(config);
      setConfig(responseConfig);
      setSavedConfig(responseConfig);
      setNotice("配置已写入后端 config.yaml，新请求将使用最新参数。");
      setSaved(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存配置失败");
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    if (!savedConfig) return;
    setConfig(savedConfig);
    setSaved(false);
    setNotice(null);
    setError(null);
  };

  const clearSearchCache = async (staleOnly: boolean) => {
    if (!staleOnly && !window.confirm("确认清空全部搜索缓存和历史结果？")) return;
    setClearingCache(staleOnly ? "stale" : "all");
    setNotice(null);
    setError(null);
    try {
      const result = await api.clearSearchCache(staleOnly);
      setNotice(
        staleOnly
          ? `已清理 ${result.deleted} 条过期搜索缓存。`
          : `已清空 ${result.deleted} 条搜索缓存。`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "清理搜索缓存失败");
    } finally {
      setClearingCache(null);
    }
  };


  const restartWebService = async () => {
    if (!window.confirm(
      "确认底层重启 kemo-graph？当前 FastAPI、调度器和后台线程会完整退出，再启动新的 Python 进程。",
    )) return;
    setRestartingService(true);
    setNotice(null);
    setError(null);
    try {
      const restart = await api.restartService();
      setNotice("旧服务正在退出；新进程启动后页面会自动恢复连接并刷新。");
      for (let attempt = 0; attempt < 90; attempt += 1) {
        await new Promise((resolve) => globalThis.setTimeout(resolve, 1_000));
        try {
          const runtime = await api.getRuntimeStatus();
          if (runtime.pid !== restart.old_pid) {
            globalThis.location.reload();
            return;
          }
        } catch {
          // 旧进程退出与新进程监听端口之间出现短暂断线是正常重启阶段。
        }
      }
        setError("新服务在 90 秒内未恢复，请检查 update/runtime/restart.log。");
      setRestartingService(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法重启 Web 服务");
      setRestartingService(false);
    }
  };

  const loadUpdateStatus = useCallback(async () => {
    try {
      setUpdateStatus(await api.getUpdateStatus());
    } catch {
      setUpdateStatus(null);
    }
  }, []);

  useEffect(() => {
    if (activeGroupId === "system") void loadUpdateStatus();
  }, [activeGroupId, loadUpdateStatus]);

  const runUpdateCheck = async () => {
    setCheckingUpdate(true);
    setNotice(null);
    setError(null);
    try {
      const result = await api.checkUpdate();
      setUpdateStatus(result);
      setChecked(true);
      setNotice(result.update_available ? "发现可用新版本。" : "当前已是最新版本。");
    } catch (caught) {
      setChecked(false);
      setError(caught instanceof Error ? caught.message : "检查更新失败");
    } finally {
      setCheckingUpdate(false);
    }
  };

  let updateButtonClass = "button button--secondary";
  let updateButtonLabel = "检查更新";
  let updateButtonIcon = <RefreshCw size={15} />;
  if (checkingUpdate) {
    updateButtonLabel = "检测中";
    updateButtonIcon = <RefreshCw className="spin" size={15} />;
  } else if (checked && updateStatus?.update_available) {
    updateButtonClass = "button button--primary";
    updateButtonLabel = `发现新版本 ${updateStatus.latest_version ?? ""}`.trim();
    updateButtonIcon = <Download size={15} />;
  } else if (checked) {
    updateButtonLabel = "已是最新";
    updateButtonIcon = <CheckCircle2 size={15} />;
  }

  return (
    <section className="settings-page page-stack">
      <PageIntro
        title="系统配置"
        actions={
          <button className="button button--secondary" disabled={loading || saving} onClick={() => void loadConfig()}>
            <RotateCcw className={loading ? "spin" : ""} size={16} />重新读取
          </button>
        }
      />

      <div className={`settings-body ${notice || error ? "has-feedback" : ""}`}>
        {notice || error ? (
          <div className="settings-feedback" aria-live="polite">
            {notice ? <InfoNotice message={notice} /> : null}
            {error ? <ErrorNotice message={error} /> : null}
          </div>
        ) : null}
        {loading ? <div className="card"><LoadingState label="正在读取配置…" /></div> : null}

        {!loading && config ? (
          <div className="settings-layout">
          <aside className="settings-nav" aria-label="配置分组">
            <div className="settings-nav__header"><strong>配置分组</strong><small>{groups.length} 组</small></div>
            <div className="settings-nav__list">
              {groups.map(({ id, title, description, icon: Icon, sections }) => (
                <button
                  className={`settings-nav__item ${activeGroup.id === id ? "is-active" : ""}`}
                  key={id}
                  type="button"
                  aria-label={title}
                  aria-pressed={activeGroup.id === id}
                  aria-controls="settings-panel"
                  onClick={() => setActiveGroupId(id)}
                >
                  <span className="settings-nav__icon"><Icon size={17} /></span>
                  <span><strong>{title}</strong><small>{description}</small></span>
                  <span className="settings-nav__count" aria-hidden="true">{sections.reduce((count, section) => count + section.fields.length, 0)}</span>
                </button>
              ))}
            </div>
          </aside>

          <div className="settings-content">
            <div className="settings-save-bar">
              <div>
                <h2 id="settings-active-title">{activeGroup.title}</h2>
                <span role="status">{dirty ? "有未保存修改" : "已保存"}</span>
              </div>
              <div className="settings-save-actions">
                <button className="button button--secondary" disabled={saving || !dirty} onClick={reset} title="恢复所有分组到上次保存的配置">
                  <RotateCcw size={16} />恢复已保存
                </button>
                <button className="button button--primary" disabled={saving || !dirty} onClick={() => void save()} title="保存全部分组的配置">
                  {saved ? <CheckCircle2 size={16} /> : <Save size={16} />}
                  {saving ? "保存中" : saved ? "已保存" : "保存全部"}
                </button>
              </div>
            </div>

            <section ref={panelRef} className="settings-panel" id="settings-panel" aria-labelledby="settings-active-title">
              {activeGroup.sections.map((section) => (
                <div className="settings-section" key={section.title}>
                  <div className="settings-section__title">
                    <h3>{section.title}</h3>
                    {section.description && <p>{section.description}</p>}
                    {activeGroup.id === "models" && section.title === "模型选择" ? (
                      <small className={`settings-model-summary ${catalogError ? "is-error" : ""}`} role="status">
                        {catalogError ?? (modelsLoading ? "正在获取模型…" : `${modelCatalog?.models.length ?? 0} 个可用模型`)}
                      </small>
                    ) : null}
                  </div>
                  <div className="settings-field-list">
                    {section.fields.map((field) => {
                      const value = getConfigValue(config, field.path);
                      return (
                        <div className={`settings-field ${field.readOnly ? "is-readonly" : ""}`} key={field.path}>
                          <div className="settings-field__meta">
                            <details className="settings-field-help">
                              <summary aria-label={`${field.label}说明`} title="查看参数说明">
                                <strong>{field.label}</strong><CircleHelp size={14} aria-hidden="true" />
                              </summary>
                              <p>{field.description}</p>
                            </details>
                          </div>
                          <div className={`settings-control ${field.suffix ? "has-suffix" : ""}`}>
                            {field.kind === "model" && field.modelTask ? (
                              <ModelPicker
                                label={field.label}
                                task={field.modelTask}
                                value={typeof value === "string" ? value : ""}
                                models={gatewayDirty ? [] : modelCatalog?.models ?? []}
                                loading={modelsLoading && !gatewayDirty}
                                error={catalogError}
                                onChange={(nextValue) => updateField(field, nextValue)}
                                onRefresh={gatewayDirty ? undefined : () => setModelsRevision((revision) => revision + 1)}
                              />
                            ) : field.kind === "select" || field.kind === "boolean" ? (
                              <ThemedSelect
                                ariaLabel={field.label}
                                value={field.kind === "boolean" ? String(Boolean(value)) : typeof value === "string" ? value : ""}
                                options={field.kind === "boolean" ? BOOLEAN_OPTIONS : field.options ?? []}
                                onChange={(nextValue) => updateField(field, nextValue)}
                                disabled={field.readOnly}
                                className="settings-themed-select"
                              />
                            ) : (
                              <input
                                aria-label={field.label}
                                type={field.kind}
                                min={field.min}
                                max={field.max}
                                step={field.step}
                                value={typeof value === "string" || typeof value === "number" ? value : ""}
                                onChange={(event) => updateField(field, event.target.value)}
                                readOnly={field.readOnly}
                                aria-readonly={field.readOnly}
                                placeholder={field.placeholder}
                                autoComplete={field.kind === "password" ? "new-password" : undefined}
                                spellCheck={false}
                              />
                            )}
                            {field.suffix ? <em>{field.suffix}</em> : null}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
              {activeGroup.id === "cache" ? (
                <div className="settings-cache-zone" role="group" aria-label="搜索缓存维护">
                  <span className="settings-cache-zone__icon"><History size={17} /></span>
                  <span>
                    <strong>搜索缓存维护</strong>
                  </span>
                  <span className="settings-cache-zone__actions">
                    <button
                      className="button button--secondary"
                      disabled={Boolean(clearingCache)}
                      onClick={() => void clearSearchCache(true)}
                      type="button"
                    >
                      {clearingCache === "stale" ? "清理中" : "清理过期"}
                    </button>
                    <button
                      className="button button--danger"
                      disabled={Boolean(clearingCache)}
                      onClick={() => void clearSearchCache(false)}
                      type="button"
                    >
                      {clearingCache === "all" ? "清空中" : "清空缓存"}
                    </button>
                  </span>
                </div>
              ) : null}
              {activeGroup.id === "system" ? (
                <div className="settings-maintenance settings-section">
                  <div className="settings-section__title">
                    <h3>维护操作</h3>
                  </div>
                  <div className="settings-update-zone settings-version-zone">
                    <span className="settings-update-zone__icon"><Tag size={18} /></span>
                    <span className="settings-update-zone__content">
                      <strong>版本检测</strong>
                      <span className="settings-version-zone__versions">
                        <span className="settings-version-zone__label">当前</span>
                        <code className="settings-version-zone__value">{updateStatus?.current_version ?? "—"}</code>
                        <span className="settings-version-zone__arrow">→</span>
                        <span className="settings-version-zone__label">最新</span>
                        <code className={`settings-version-zone__value${updateStatus?.update_available ? " is-latest" : ""}`}>
                          {updateStatus?.latest_version ?? "未检测"}
                        </code>
                      </span>
                    </span>
                    <button
                      className={updateButtonClass}
                      disabled={checkingUpdate}
                      onClick={() => void runUpdateCheck()}
                      type="button"
                    >
                      {updateButtonIcon}
                      {updateButtonLabel}
                    </button>
                  </div>
                  <div className="settings-update-zone settings-restart-zone">
                    <span className="settings-update-zone__icon"><Power size={18} /></span>
                    <span className="settings-update-zone__content">
                      <strong>服务重启</strong>
                      <small>重启会短暂断开连接，请先保存并等待任务完成。</small>
                    </span>
                    <button
                      className="button button--secondary"
                      disabled={restartingService}
                      onClick={() => void restartWebService()}
                      type="button"
                    >
                      <Power size={15} />
                      {restartingService ? "重启中" : "重启服务"}
                    </button>
                  </div>
                </div>
              ) : null}
            </section>
          </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
