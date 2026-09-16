import { Activity, FileSearch, RefreshCw, Terminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/api";
import { ThemedDatePicker } from "./ThemedDatePicker";
import type { LogCategory, RuntimeLogs } from "../types/api";

const tabs = [
  { value: "terminal", label: "终端启动日志", icon: Terminal, hint: "Web 启停与 Python / Uvicorn 日志；从启用记录后的服务启动开始保存，不含 shell 历史或未接入 logging 的直接输出。" },
  { value: "query", label: "查询日志", icon: FileSearch, hint: "检索开始、完成、失败、耗时与缓存事件；使用 query_id 关联一次查询，不额外记录提问正文或模型回答。" },
  { value: "internal", label: "内部运行日志", icon: Activity, hint: "文档导入、图谱构建、向量索引、删除、维护及其他内部运行事件。" },
] as const;

export function RuntimeLogPanel() {
  const [category, setCategory] = useState<LogCategory>("terminal");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [data, setData] = useState<RuntimeLogs | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [follow, setFollow] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!date) { setLoading(false); return; }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const load = async () => {
      if (!active || !date) return;
      // Refresh after completion, not with overlapping interval requests.
      if (!document.hidden) {
        setLoading(true);
        try {
          const next = await api.getSystemLogs(category, date, controller.signal);
          if (active) { setData(next); setError(null); setUpdatedAt(new Date().toLocaleTimeString()); }
        } catch (caught) {
          if (active) setError(caught instanceof Error ? caught.message : "无法读取日志");
        } finally { if (active) setLoading(false); }
      }
      if (active && autoRefresh) timer = setTimeout(() => void load(), 5000);
    };
    void load();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [category, date, autoRefresh, refresh]);
  const visibleData = data?.category === category && data?.date === date ? data : null;
  useEffect(() => {
    if (follow && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [visibleData, follow]);
  const selected = tabs.find((tab) => tab.value === category)!;
  return <section className="runtime-log-panel card" aria-label="运行日志">
    <header className="runtime-log-panel__header">
      <div><p className="eyebrow">Runtime logs</p><h3>运行日志</h3></div>
      <div className="runtime-log-panel__controls">
        <label>日期（UTC）<ThemedDatePicker ariaLabel="日志日期（UTC）" value={date} onChange={(next) => { setDate(next); setError(null); setFollow(true); }} /></label>
        <label><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />自动刷新</label>
        <label><input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />跟随最新</label>
        <button className="button button--secondary" type="button" disabled={loading || !date} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新日志</button>
      </div>
    </header>
    <div className="runtime-log-tabs" role="tablist" aria-label="日志类型">
      {tabs.map(({ value, label, icon: Icon }, index) => <button type="button" role="tab" key={value} id={`runtime-log-tab-${value}`} aria-selected={category === value} tabIndex={category === value ? 0 : -1} aria-controls="runtime-log-content"
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowLeft" ? -1 : 1) + tabs.length) % tabs.length;
          setCategory(tabs[next].value); setError(null); setFollow(true);
          document.getElementById(`runtime-log-tab-${tabs[next].value}`)?.focus();
        }} onClick={() => { setCategory(value); setError(null); setFollow(true); }}><Icon size={16} />{label}</button>)}
    </div>
    <p className="runtime-log-hint">{selected.hint}</p>
    {error && <p className="runtime-log-error" role="alert">{error} {visibleData ? "下方保留上次成功读取的内容。" : ""}</p>}
    <div className="runtime-log-content" ref={viewport} role="tabpanel" id="runtime-log-content" aria-labelledby={`runtime-log-tab-${category}`} tabIndex={0}
      onScroll={(event) => { const el = event.currentTarget; setFollow(el.scrollHeight - el.clientHeight - el.scrollTop < 32); }}>
      {visibleData?.entries.map((entry) => {
        const level = entry.level.toLowerCase();
        const tone = level.startsWith("err") ? "is-error" : level.startsWith("warn") ? "is-warning" : "is-info";
        const tag = tone === "is-error" ? "ERR" : tone === "is-warning" ? "WRN" : "OUT";
        return <div className={`runtime-log-line ${tone}`} key={entry.id}>
          <time>{entry.time}</time>
          <span className="runtime-log-line__tag">{tag}</span>
          <code className="runtime-log-line__body" title={`${entry.module} / ${entry.action}`}>{entry.detail}</code>
          {entry.elapsed_ms !== "-" && <small className="runtime-log-line__elapsed">{entry.elapsed_ms} ms</small>}
        </div>;
      })}
      {!visibleData?.entries.length && <div className="empty-compact"><selected.icon size={25} /><strong>{loading ? "正在读取日志…" : "暂无此类日志"}</strong><span>{!date ? "请选择日志日期。" : error ? "读取失败，请检查服务连接后重试。" : "可切换日期或等待新的运行事件；历史终端输出无法回补。"}</span></div>}
    </div>
    <footer className="runtime-log-footer" role="status"><span>{visibleData?.entries.length ?? 0} 条 · 最多展示最近 200 条 · 日志时间为 UTC</span><span>{loading ? "刷新中…" : updatedAt ? `上次读取 ${updatedAt}` : "尚未读取"}{autoRefresh ? " · 每 5 秒刷新" : " · 自动刷新已暂停"}</span></footer>
    {visibleData?.truncated && <p className="runtime-log-hint">已限制显示量：仅扫描所选日期文件末尾 2 MB，并展示其中最新的匹配记录。更早内容请查看服务端原始日志。</p>}
  </section>;
}
