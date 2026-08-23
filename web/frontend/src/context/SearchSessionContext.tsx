import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api } from "../api/api";
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
  resultDisplayMode: SearchResultDisplayMode;
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
  resultDisplayMode: "markdown",
};

const SearchSessionContext = createContext<SearchSessionValue | null>(null);

function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "检索失败";
}

export function SearchSessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SearchSessionState>(initialState);
  const requestSequence = useRef(0);

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
      setState((current) => ({
        ...current,
        query: normalizedQuery,
        mode: requestedMode,
        result: null,
        resultMode: null,
        loading: true,
        error: null,
      }));

      try {
        const data =
          requestedMode === "answer"
            ? await api.queryAnswer(normalizedQuery, {
                graph_depth: 3,
                rag_top_k: 10,
                force,
              })
            : requestedMode === "graph"
              ? await api.queryGraph(normalizedQuery, {
                  depth: 3,
                  direction: "both",
                  force,
                })
              : requestedMode === "rag"
                ? await api.queryRag(normalizedQuery, { top_k: 10, force })
                : requestedMode === "global"
                  ? await api.queryGlobal(normalizedQuery, { top_k: 5, force })
                  : await api.queryHybrid(normalizedQuery, {
                      graph_depth: 3,
                      rag_top_k: 10,
                      force,
                    });

        // A newer search or a mode switch owns the visible state. The older
        // request is still allowed to complete on the network, but must not
        // overwrite the newer result.
        if (sequence !== requestSequence.current) return null;
        setState((current) => ({
          ...current,
          result: data,
          resultMode: requestedMode,
          loading: false,
          error: null,
        }));
        return data;
      } catch (caught) {
        if (sequence !== requestSequence.current) return null;
        setState((current) => ({
          ...current,
          loading: false,
          error: errorMessage(caught),
        }));
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
