import { CheckCircle2, Circle, Clock3, LoaderCircle, AlertCircle, History } from "lucide-react";
import { useEffect, useState } from "react";
import type { SearchMode, QueryProgressStep } from "../types/api";

export type SearchProgress = {
  query: string;
  mode: SearchMode;
  status: "running" | "completed" | "failed" | "restored";
  startedAt: number;
  finishedAt: number | null;
  force: boolean;
  progressId?: string;
  steps?: QueryProgressStep[];
};

export const searchModeHelp: Record<SearchMode, { label: string; description: string; processing: string }> = {
  answer: { label: "LLM 回答", description: "先结合图谱关系与文档片段查找依据，再由模型整理回答。适合直接提问。", processing: "本次流程包含混合检索与模型回答生成；正在等待服务端返回。" },
  graph: { label: "图谱检索", description: "查找知识节点、关联实体和关系路径。适合了解“谁与谁有什么关系”。", processing: "本次流程包含实体匹配与关联关系查询；正在等待图谱结果。" },
  rag: { label: "向量检索", description: "按语义查找原文片段与上下文。即使措辞不同，也可以尝试查找相关资料。", processing: "本次流程包含语义召回、排序与筛选；正在等待文档片段。" },
  hybrid: { label: "混合检索", description: "同时查看图谱关系和文档片段，不额外生成综合回答。适合核对资料。", processing: "本次流程包含图谱与向量检索；正在等待两类检索结果。" },
  global: { label: "全局检索", description: "基于节点群总结回答跨主题问题。使用前，请在知识图谱页点击“总结节点群”。", processing: "本次流程包含节点群摘要检索与综合回答；正在等待服务端返回。" },
};

export function SearchProgressCard({ progress }: { progress: SearchProgress }) {
  const [now, setNow] = useState(Date.now);
  const running = progress.status === "running";
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, progress.startedAt]);
  const elapsed = Math.max(0, Math.floor(((progress.finishedAt ?? now) - progress.startedAt) / 1000));
  const restored = progress.status === "restored";
  const failed = progress.status === "failed";
  const title = restored ? "历史结果" : failed ? "检索失败" : running ? "检索中" : "检索完成";
  const Icon = restored ? History : failed ? AlertCircle : running ? LoaderCircle : CheckCircle2;
  return (
    <section className={`search-progress card is-${progress.status}`} aria-label="检索流程状态">
      <header className="search-progress__header">
        <Icon size={21} className={running ? "spin" : undefined} aria-hidden="true" />
        <div role="status"><strong>{title}</strong><span>{searchModeHelp[progress.mode].label}{restored ? " · 历史快照" : progress.force ? " · 跳过缓存" : ""}</span></div>
        {!restored && <span className="search-progress__time"><Clock3 size={14} />{running ? "已等待" : "耗时"} {elapsed} 秒</span>}
      </header>
      <p className="search-progress__query" title={progress.query}>{progress.query}</p>
      {!restored && <ol className="search-progress__steps">
        <li className="is-done"><CheckCircle2 size={16} /><span>已提交</span></li>
        {progress.steps?.map((step) => {
          const active = step.status === "running" && running;
          const done = step.status === "completed";
          const problem = step.status === "failed" || step.status === "fallback";
          return <li key={step.id} className={active ? "is-current" : done ? "is-done" : problem ? "is-failed" : ""} aria-current={active ? "step" : undefined}>
            {active ? <LoaderCircle size={16} className="spin" /> : done ? <CheckCircle2 size={16} /> : problem ? <AlertCircle size={16} /> : <Circle size={16} />}
            <span>{step.label}{step.status === "fallback" ? " · 已回退" : step.status === "failed" ? " · 失败" : step.status === "running" && !running ? " · 未确认" : ""}</span>
          </li>;
        })}
        {!progress.steps?.length && <li className={running ? "is-current" : failed ? "is-failed" : "is-done"} aria-current={running ? "step" : undefined}>
          {running ? <LoaderCircle size={16} className="spin" /> : failed ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
          <span>{failed ? "处理失败" : running ? "等待返回" : "已返回"}</span>
        </li>}
        <li className={!running && !failed ? "is-done" : ""}>
          {!running && !failed ? <CheckCircle2 size={16} /> : <Circle size={16} />}<span>{failed ? "无结果" : running ? "待展示" : "已展示"}</span>
        </li>
      </ol>}
      <p className="search-progress__hint">
        {running ? `${elapsed >= 30 ? "等待较久，请稍候。" : ""}可切换页面，请勿刷新或关闭网页。` : failed ? "请查看错误信息后重试。" : restored ? "历史缓存，点击“重新检索”可更新。" : "结果与来源见下方。"}
      </p>
    </section>
  );
}
