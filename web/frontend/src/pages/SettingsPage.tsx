import {
  CheckCircle2,
  ChevronRight,
  Database,
  KeyRound,
  Network,
  RotateCcw,
  Save,
  ServerCog,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/api";
import { ErrorNotice, InfoNotice, LoadingState } from "../components/Feedback";
import { PageIntro } from "../components/PageIntro";
import type { ConfigData } from "../types/api";

type FieldKind = "number" | "select" | "text" | "time";

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
};

type ConfigSection = {
  title: string;
  description: string;
  fields: ConfigField[];
};

type ConfigGroup = {
  id: string;
  title: string;
  description: string;
  icon: typeof Network;
  tone: "cyan" | "amber" | "purple" | "blue";
  sections: ConfigSection[];
};

const groups: ConfigGroup[] = [
  {
    id: "graph",
    title: "图谱参数",
    description: "检索深度、实体抽取与智能体限制",
    icon: Network,
    tone: "cyan",
    sections: [
      {
        title: "图谱检索与展示",
        description: "控制查询扩展范围、命中门槛和前端图谱的加载规模。",
        fields: [
          { path: "max_query_depth", label: "最大查询深度", description: "图谱查询允许扩展的最大 BFS 层数。", kind: "number", min: 1, max: 10, step: 1, suffix: "层" },
          { path: "default_confidence", label: "默认置信度", description: "实体匹配与图谱检索的默认最低置信度。", kind: "number", min: 0, max: 1, step: 0.05 },
          { path: "web_node_load_depth", label: "网页节点加载深度", description: "打开图谱页面时默认加载的邻居层数。", kind: "number", min: 0, max: 10, step: 1, suffix: "层" },
          { path: "web_node_label_threshold", label: "节点标签阈值", description: "达到此引用次数的节点优先显示标签。", kind: "number", min: 0, step: 1, suffix: "次引用" },
        ],
      },
      {
        title: "实体抽取智能体",
        description: "控制文档整理时的实体抽取方式和工具调用上限。",
        fields: [
          { path: "entity_extraction.method", label: "实体抽取方式", description: "LLM 模式使用模型理解语义；规则模式适合轻量处理。", kind: "select", options: [{ label: "LLM 智能抽取", value: "llm" }, { label: "规则抽取", value: "rule" }] },
          { path: "entity_extraction.max_entities", label: "单次最大实体数", description: "每次查询或抽取最多保留的实体数量。", kind: "number", min: 1, max: 100, step: 1, suffix: "个" },
          { path: "graph_tool_max_iterations", label: "工具调用最大轮次", description: "图谱构建智能体在单篇文档中的最大工具循环次数。", kind: "number", min: 1, max: 200, step: 1, suffix: "轮" },
        ],
      },
    ],
  },
  {
    id: "rag",
    title: "RAG 参数",
    description: "切片、向量召回、重排与混合增强",
    icon: Database,
    tone: "amber",
    sections: [
      {
        title: "切片与向量召回",
        description: "定义固定或分层切片，以及向量候选的数量和最低相似度。",
        fields: [
          { path: "chunking_mode", label: "切片模式", description: "分层模式同时建立小、中、大三类向量；固定模式仅使用中粒度。", kind: "select", options: [{ label: "分层切片", value: "hierarchical" }, { label: "固定切片", value: "fixed" }] },
          { path: "chunk_small_size", label: "小粒度切片", description: "负责关键词、实体和精确片段召回。", kind: "number", min: 64, max: 2048, step: 1, suffix: "tokens" },
          { path: "chunk_size", label: "中粒度切片", description: "负责段落级语义与命中后的主要上下文。", kind: "number", min: 128, max: 4096, step: 1, suffix: "tokens" },
          { path: "chunk_large_size", label: "大粒度切片", description: "负责章节主题召回和较完整的上下文。", kind: "number", min: 256, max: 8192, step: 1, suffix: "tokens" },
          { path: "chunk_overlap", label: "中粒度重叠", description: "中粒度的重叠 token 数；分层模式会按相同比例计算其他层级。", kind: "number", min: 0, max: 512, step: 1, suffix: "tokens" },
          { path: "default_top_k", label: "默认召回数量", description: "向量检索默认取回的候选片段数量。", kind: "number", min: 1, step: 1, suffix: "条" },
          { path: "rag_similarity_threshold", label: "相似度阈值", description: "低于该分数的向量候选不会进入结果集。", kind: "number", min: 0, max: 1, step: 0.05 },
        ],
      },
      {
        title: "重排与混合检索",
        description: "控制 Rerank 结果规模和图谱锚定片段的增强幅度。",
        fields: [
          { path: "rerank_top_n", label: "重排保留数量", description: "Rerank 后最终保留的候选片段数量。", kind: "number", min: 1, step: 1, suffix: "条" },
          { path: "hybrid_enhancement_factor", label: "混合增强系数", description: "图谱锚定 chunk 的 FAISS 分数增强倍率。", kind: "number", min: 0, step: 0.1, suffix: "倍" },
        ],
      },
    ],
  },
  {
    id: "models",
    title: "网关与模型",
    description: "统一 Kemo 网关与三类模型选择",
    icon: KeyRound,
    tone: "purple",
    sections: [
      {
        title: "Kemo 网关",
        description: "所有模型能力统一通过此网关调用，API Key 字段仅填写环境变量名。",
        fields: [
          { path: "kemo.base_url", label: "网关 Base URL", description: "Kemo Gateway 的根地址。", kind: "text" },
          { path: "kemo.api_key_env", label: "API Key 环境变量", description: "保存密钥的环境变量名称，不要填写真实密钥。", kind: "text" },
          { path: "kemo.protocol_version", label: "协议版本", description: "随请求发送的 Kemo 协议版本。", kind: "text" },
          { path: "kemo.request_timeout", label: "请求超时", description: "单次 Kemo 模型请求的最长等待时间。", kind: "number", min: 1, step: 1, suffix: "秒" },
        ],
      },
      {
        title: "模型选择",
        description: "三类模型共享上方网关和密钥，仅分别配置模型标识。",
        fields: [
          { path: "models.llm", label: "LLM 模型", description: "用于实体抽取、图谱构建和总结生成。", kind: "text" },
          { path: "models.embedding", label: "Embedding 模型", description: "用于生成文档和查询向量。", kind: "text" },
          { path: "models.embedding_dimensions", label: "向量维度", description: "模型返回的向量长度；修改后需要重建向量索引。", kind: "number", min: 1, step: 1, suffix: "维" },
          { path: "models.rerank", label: "Rerank 模型", description: "用于对向量召回候选进行相关性重排。", kind: "text" },
        ],
      },
    ],
  },
  {
    id: "system",
    title: "系统维护",
    description: "文档容量、定时任务、回收站与日志",
    icon: ServerCog,
    tone: "blue",
    sections: [
      {
        title: "知识库维护",
        description: "控制文档容量以及摘要和回收站后台任务。",
        fields: [
          { path: "max_documents", label: "最大文档数", description: "知识库允许管理的最大文档数量。", kind: "number", min: 1, step: 1, suffix: "篇" },
          { path: "summary_trigger_file_count", label: "摘要触发文件数", description: "累计达到此数量后触发群组摘要生成。", kind: "number", min: 1, step: 1, suffix: "篇" },
          { path: "summary_trigger_time", label: "每日摘要时间", description: "后台调度器每日运行群组摘要任务的本地时间。", kind: "time" },
          { path: "recycle_life_days", label: "回收站保留时间", description: "过期文件将在后台调度任务中自动清理。", kind: "number", min: 0, step: 1, suffix: "天" },
        ],
      },
      {
        title: "运行日志",
        description: "设置日志目录和最低记录级别。",
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
        if (field.kind === "number") {
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
  if (getConfigValue(config, "chunking_mode") === "hierarchical") {
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
  const [activeGroupId, setActiveGroupId] = useState(groups[0].id);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [emptyingRecycle, setEmptyingRecycle] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const panelRef = useRef<HTMLElement>(null);

  const activeGroup = groups.find((group) => group.id === activeGroupId) ?? groups[0];
  const dirty = useMemo(
    () => Boolean(config && savedConfig && JSON.stringify(config) !== JSON.stringify(savedConfig)),
    [config, savedConfig],
  );

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
    panelRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [activeGroupId]);

  const updateField = (field: ConfigField, rawValue: string) => {
    if (!config) return;
    const nextValue = field.kind === "number" && rawValue !== "" ? Number(rawValue) : rawValue;
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
      setNotice("配置已写入后端 config.json，新请求将使用最新参数。");
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

  const emptyRecycle = async () => {
    if (!window.confirm("确认永久清空回收站？其中的全部文件将被删除，且无法恢复。")) {
      return;
    }

    setEmptyingRecycle(true);
    setNotice(null);
    setError(null);
    try {
      const result = await api.emptyRecycle();
      setNotice(`回收站已清空，共永久删除 ${result.deleted} 个文件。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "清空回收站失败");
    } finally {
      setEmptyingRecycle(false);
    }
  };

  return (
    <section className="settings-page page-stack">
      <PageIntro
        title="配置"
        description="通过结构化字段管理图谱、RAG、Kemo 网关、模型与系统维护参数。"
        actions={
          <button className="button button--secondary" disabled={loading || saving} onClick={() => void loadConfig()}>
            <RotateCcw className={loading ? "spin" : ""} size={16} />重新读取
          </button>
        }
      />

      {notice ? <InfoNotice message={notice} /> : null}
      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <div className="card"><LoadingState label="正在读取 config.json…" /></div> : null}

      {!loading && config ? (
        <div className="settings-layout">
          <aside className="settings-nav card" aria-label="配置分组">
            {groups.map(({ id, title, description, icon: Icon, tone }) => (
              <button
                className={`settings-nav__item tone-${tone} ${activeGroup.id === id ? "is-active" : ""}`}
                key={id}
                onClick={() => setActiveGroupId(id)}
              >
                <span className="settings-nav__icon"><Icon size={18} /></span>
                <span><strong>{title}</strong><small>{description}</small></span>
                <ChevronRight size={15} />
              </button>
            ))}
          </aside>

          <div className="settings-content card">
            <div className="settings-save-bar">
              <div>
                <strong>{dirty ? "有尚未保存的修改" : "配置已与服务端同步"}</strong>
                <span>{dirty ? "检查修改后保存，未知配置字段会原样保留。" : "选择左侧分组以查看和调整参数。"}</span>
              </div>
              <div className="settings-save-actions">
                <button className="button button--secondary" disabled={saving || !dirty} onClick={reset}>
                  <RotateCcw size={16} />恢复已保存
                </button>
                <button className="button button--primary" disabled={saving || !dirty} onClick={() => void save()}>
                  {saved ? <CheckCircle2 size={16} /> : <Save size={16} />}
                  {saving ? "保存中" : saved ? "已保存" : "保存配置"}
                </button>
              </div>
            </div>

            <section ref={panelRef} className={`settings-panel tone-${activeGroup.tone}`}>
              {activeGroup.sections.map((section) => (
                <div className="settings-section" key={section.title}>
                  <div className="settings-section__title">
                    <h3>{section.title}</h3>
                    <p>{section.description}</p>
                  </div>
                  <div className="settings-field-list">
                    {section.fields.map((field) => {
                      const value = getConfigValue(config, field.path);
                      return (
                        <label className="settings-field" key={field.path}>
                          <span className="settings-field__meta">
                            <strong>{field.label}</strong>
                            <small>{field.description}</small>
                            <code>{field.path}</code>
                          </span>
                          <span className="settings-control">
                            {field.kind === "select" ? (
                              <select
                                aria-label={field.label}
                                value={typeof value === "string" ? value : ""}
                                onChange={(event) => updateField(field, event.target.value)}
                              >
                                {field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                              </select>
                            ) : (
                              <input
                                aria-label={field.label}
                                type={field.kind}
                                min={field.min}
                                max={field.max}
                                step={field.step}
                                value={typeof value === "string" || typeof value === "number" ? value : ""}
                                onChange={(event) => updateField(field, event.target.value)}
                                spellCheck={false}
                              />
                            )}
                            {field.suffix ? <em>{field.suffix}</em> : null}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
              {activeGroup.id === "system" ? (
                <div className="settings-danger-zone">
                  <span>
                    <strong>清空回收站</strong>
                    <small>永久删除回收站中的全部文件，此操作不可恢复。</small>
                  </span>
                  <button
                    className="button button--danger"
                    disabled={emptyingRecycle}
                    onClick={() => void emptyRecycle()}
                    type="button"
                  >
                    <Trash2 size={16} />
                    {emptyingRecycle ? "正在清空" : "清空回收站"}
                  </button>
                </div>
              ) : null}
            </section>
          </div>
        </div>
      ) : null}
    </section>
  );
}
