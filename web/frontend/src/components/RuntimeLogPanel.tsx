import { Activity, FileSearch, RefreshCw, Terminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/api";
import { ThemedDatePicker } from "./ThemedDatePicker";
import type { LogCategory, RuntimeLogs } from "../types/api";

const tabs = [
  { value: "terminal", label: "终端启动日志", icon: Terminal, hint: "服务启停与终端运行记录" },
  { value: "query", label: "查询日志", icon: FileSearch, hint: "检索进度、耗时与缓存记录" },
  { value: "internal", label: "内部运行日志", icon: Activity, hint: "文档处理、构建与维护记录" },
] as const;

export function RuntimeLogPanel({ active = true }: { active?: boolean }) {
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
    if (!active || !date) { setLoading(false); return; }
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const load = async () => {
      if (!current || !date) return;
      // Refresh after completion, not with overlapping interval requests.
      if (!document.hidden) {
        setLoading(true);
        try {
          const next = await api.getSystemLogs(category, date, controller.signal);
          if (current) { setData(next); setError(null); setUpdatedAt(new Date().toLocaleTimeString()); }
        } catch (caught) {
          if (current) setError(caught instanceof Error ? caught.message : "无法读取日志");
        } finally { if (current) setLoading(false); }
      }
      if (current && autoRefresh) timer = setTimeout(() => void load(), 5000);
    };
    void load();
    return () => { current = false; controller.abort(); clearTimeout(timer); };
  }, [active, category, date, autoRefresh, refresh]);
  const visibleData = data?.category === category && data?.date === date ? data : null;
  useEffect(() => {
    if (follow && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [visibleData, follow]);
  const selected = tabs.find((tab) => tab.value === category)!;
  return <section className="runtime-log-panel card" aria-label="运行日志">
    <header className="runtime-log-panel__header">
      <div><h2>运行日志</h2></div>
      <div className="runtime-log-panel__controls">
        <label>日期（UTC）<ThemedDatePicker ariaLabel="日志日期（UTC）" value={date} onChange={(next) => { setDate(next); setError(null); setFollow(true); }} /></label>
        <label><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />自动刷新</label>
        <label><input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />跟随最新</label>
        <button className="button button--secondary" type="button" disabled={loading || !date} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新日志</button>
      </div>
    </header>
    <div className="runtime-log-tabs" role="tablist" aria-label="日志类型">
      {tabs.map(({ value, label, icon: Icon, hint }, index) => <button type="button" role="tab" key={value} title={hint} id={`runtime-log-tab-${value}`} aria-selected={category === value} tabIndex={category === value ? 0 : -1} aria-controls="runtime-log-content"
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowLeft" ? -1 : 1) + tabs.length) % tabs.length;
          setCategory(tabs[next].value); setError(null); setFollow(true);
          document.getElementById(`runtime-log-tab-${tabs[next].value}`)?.focus();
        }} onClick={() => { setCategory(value); setError(null); setFollow(true); }}><Icon size={16} />{label}</button>)}
    </div>
    {error && <p className="runtime-log-error" role="alert">{error} {visibleData ? "下方保留上次成功读取的内容。" : ""}</p>}
    <div className="runtime-log-content" ref={viewport} role="tabpanel" id="runtime-log-content" aria-labelledby={`runtime-log-tab-${category}`} tabIndex={0}
      onScroll={(event) => { const el = event.currentTarget; setFollow(el.scrollHeight - el.clientHeight - el.scrollTop < 32); }}>
      <table className="runtime-log-table" aria-label={`${selected.label}表格`} aria-busy={loading}>
        <thead><tr>
          <th scope="col">时间（UTC）</th>
          <th scope="col">级别</th>
          <th scope="col">模块</th>
          <th scope="col">动作</th>
          <th scope="col">详情</th>
          <th scope="col">耗时（ms）</th>
        </tr></thead>
        <tbody>
          {visibleData?.entries.map((entry) => {
            const tone = entry.level === "ERROR" ? "is-error" : entry.level === "WARNING" ? "is-warning" : "is-info";
            return <tr className={tone} key={entry.id}>
              <td><time>{entry.time}</time></td>
              <td><span className="runtime-log-table__level">{entry.level}</span></td>
              <td><code>{entry.module}</code></td>
              <td><code>{entry.action}</code></td>
              <td className="runtime-log-table__detail"><code>{entry.detail}</code></td>
              <td className="runtime-log-table__elapsed">{entry.elapsed_ms}</td>
            </tr>;
          })}
          {!visibleData?.entries.length && <tr><td colSpan={6}><div className="empty-compact"><selected.icon size={25} /><strong>{loading ? "正在读取日志…" : "暂无此类日志"}</strong><span>{!date ? "请选择日期" : error ? "请检查连接后重试" : "切换日期查看"}</span></div></td></tr>}
        </tbody>
      </table>
    </div>
    <footer className="runtime-log-footer" role="status"><span>{visibleData?.entries.length ?? 0} 条 · 上限 200 条</span><span>{loading ? "刷新中…" : updatedAt ? `更新于 ${updatedAt}` : "尚未读取"}{autoRefresh ? " · 每 5 秒刷新" : " · 已暂停"}</span></footer>
    {visibleData?.truncated && <p className="runtime-log-hint">仅显示近期记录，更早内容请查看服务端日志。</p>}
  </section>;
}
