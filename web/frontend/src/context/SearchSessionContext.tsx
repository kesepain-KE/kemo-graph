import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api } from "../api/api";
import type { SearchProgress } from "../components/SearchProgressCard";
import type {
  AnswerQueryData,
  GlobalQueryData,
  GraphQueryData,
  HybridQueryData,
  RagQueryData,
  SearchMode,
} from "../types/api";

export type SearchResult =
  | AnswerQueryData
  | GraphQueryData
  | RagQueryData
  | HybridQueryData
  | GlobalQueryData;

export type SearchResultDisplayMode = "markdown" | "source";

type SearchSessionState = {
  query: string;
  mode: SearchMode;
  result: SearchResult | null;
  resultMode: SearchMode | null;
  loading: boolean;
  error: string | null;
  progress: SearchProgress | null;
  resultDisplayMode: SearchResultDisplayMode;
  historyRevision: number;
};

type SearchSessionValue = SearchSessionState & {
  setQuery: (query: string) => void;
  selectMode: (mode: SearchMode) => void;
  setResultDisplayMode: (mode: SearchResultDisplayMode) => void;
  runSearch: (
    query: string,
    mode: SearchMode,
    force?: boolean,
  ) => Promise<SearchResult | null>;
  restoreResult: (
    query: string,
    mode: SearchMode,
    result: SearchResult,
  ) => void;
};

const initialState: SearchSessionState = {
  query: "",
  mode: "answer",
  result: null,
  resultMode: null,
  loading: false,
  error: null,
  progress: null,
  resultDisplayMode: "markdown",
  historyRevision: 0,
};

const SearchSessionContext = createContext<SearchSessionValue | null>(null);

function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "检索失败";
}

function createProgressId(): string | undefined {
  const browserCrypto = globalThis.crypto;
  if (!browserCrypto) return undefined;
  if (typeof browserCrypto.randomUUID === "function") return browserCrypto.randomUUID();
  if (typeof browserCrypto.getRandomValues !== "function") return undefined;
  const bytes = browserCrypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

export function SearchSessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SearchSessionState>(initialState);
  const requestSequence = useRef(0);

  useEffect(() => {
    const id = state.progress?.progressId;
    if (!state.loading || !id) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const trace = await api.getQueryProgress(id, controller.signal);
        if (active && trace.available) setState((current) => current.progress?.progressId === id
          ? { ...current, progress: { ...current.progress, steps: trace.steps } } : current);
      } catch { /* Telemetry failures never interrupt the actual search. */ }
      if (active) timer = setTimeout(() => void poll(), 1000);
    };
    void poll();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [state.loading, state.progress?.progressId]);

  const setQuery = useCallback((query: string) => {
    setState((current) => ({ ...current, query }));
  }, []);

  const selectMode = useCallback((mode: SearchMode) => {
    // Changing modes invalidates a previous response for the old mode. The
    // network request itself is intentionally allowed to finish in the
    // application-level provider, so leaving and returning to the page never
    // interrupts an in-flight search.
    requestSequence.current += 1;
    setState((current) => ({
      ...current,
      mode,
      result: null,
      resultMode: null,
      error: null,
      loading: false,
      progress: null,
    }));
  }, []);

  const setResultDisplayMode = useCallback((resultDisplayMode: SearchResultDisplayMode) => {
    setState((current) => ({ ...current, resultDisplayMode }));
  }, []);

  const runSearch = useCallback(
    async (rawQuery: string, requestedMode: SearchMode, force = false) => {
      const normalizedQuery = rawQuery.trim();
      if (!normalizedQuery) return null;

      const sequence = ++requestSequence.current;
      const progressId = createProgressId();
      setState((current) => ({
        ...current,
        query: normalizedQuery,
        mode: requestedMode,
        result: null,
        resultMode: null,
        loading: true,
        error: null,
        progress: { query: normalizedQuery, mode: requestedMode, status: "running", startedAt: Date.now(), finishedAt: null, force, progressId, steps: [] },
      }));

      try {
        const data =
          requestedMode === "answer"
            ? await api.queryAnswer(normalizedQuery, {
                graph_depth: 3,
                rag_top_k: 10,
                force,
              }, progressId)
            : requestedMode === "graph"
              ? await api.queryGraph(normalizedQuery, {
                  depth: 3,
                  direction: "both",
                  force,
                }, progressId)
              : requestedMode === "rag"
                ? await api.queryRag(normalizedQuery, { top_k: 10, force }, progressId)
                : requestedMode === "global"
                  ? await api.queryGlobal(normalizedQuery, { top_k: 5, force }, progressId)
                  : await api.queryHybrid(normalizedQuery, {
                      graph_depth: 3,
                      rag_top_k: 10,
                      force,
                    }, progressId);

        // A newer search or a mode switch owns the visible state. The older
        // request is still allowed to complete on the network, but must not
        // overwrite the newer result.
        if (sequence !== requestSequence.current) {
          // The result no longer owns the visible search state, but the
          // server-side cache/history may still have changed.
          setState((current) => ({
            ...current,
            historyRevision: current.historyRevision + 1,
          }));
          return null;
        }
        setState((current) => ({
          ...current,
          result: data,
          resultMode: requestedMode,
          loading: false,
          error: null,
          progress: current.progress ? { ...current.progress, status: "completed", finishedAt: Date.now() } : null,
          historyRevision: current.historyRevision + 1,
        }));
        if (progressId) void api.getQueryProgress(progressId).then((trace) => {
          if (sequence === requestSequence.current && trace.available) setState((current) => current.progress?.progressId === progressId
            ? { ...current, progress: { ...current.progress, steps: trace.steps } } : current);
        }).catch(() => {});
        return data;
      } catch (caught) {
        if (sequence !== requestSequence.current) return null;
        setState((current) => ({
          ...current,
          loading: false,
          error: errorMessage(caught),
          progress: current.progress ? { ...current.progress, status: "failed", finishedAt: Date.now() } : null,
        }));
        if (progressId) void api.getQueryProgress(progressId).then((trace) => {
          if (sequence === requestSequence.current && trace.available) setState((current) => current.progress?.progressId === progressId
            ? { ...current, progress: { ...current.progress, steps: trace.steps } } : current);
        }).catch(() => {});
        return null;
      }
    },
    [],
  );

  const restoreResult = useCallback(
    (query: string, mode: SearchMode, result: SearchResult) => {
      requestSequence.current += 1;
      setState((current) => ({
        ...current,
        query,
        mode,
        result,
        resultMode: mode,
        loading: false,
        error: null,
        progress: { query, mode, status: "restored", startedAt: Date.now(), finishedAt: Date.now(), force: false },
      }));
    },
    [],
  );

  const value = useMemo(
    () => ({
      ...state,
      setQuery,
      selectMode,
      setResultDisplayMode,
      runSearch,
      restoreResult,
    }),
    [
      restoreResult,
      runSearch,
      selectMode,
      setQuery,
      setResultDisplayMode,
      state,
    ],
  );

  return (
    <SearchSessionContext.Provider value={value}>
      {children}
    </SearchSessionContext.Provider>
  );
}

export function useSearchSession(): SearchSessionValue {
  const value = useContext(SearchSessionContext);
  if (!value) throw new Error("useSearchSession 必须在 SearchSessionProvider 内使用");
  return value;
}
