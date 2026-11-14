import { Check, ChevronDown, RefreshCw, Search } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import type { GatewayModel, ModelTask } from "../types/api";

type Props = {
  label: string;
  task: ModelTask;
  value: string;
  models: GatewayModel[];
  loading: boolean;
  error: string | null;
  onChange: (value: string) => void;
  onRefresh?: () => void;
};

export function ModelPicker({ label, task, value, models, loading, error, onChange, onRefresh }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<CSSProperties | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const menuId = useId();
  const listboxId = `${menuId}-list`;
  const positioned = position !== null;
  const availableModels = models.filter((model) => model.task === task);
  const keyword = query.trim().toLocaleLowerCase();
  const filteredModels = availableModels.filter((model) =>
    `${model.id} ${model.provider_id}`.toLocaleLowerCase().includes(keyword),
  );

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus({ preventScroll: true });
  };

  const choose = (model: GatewayModel) => {
    onChange(model.id);
    close(true);
  };

  const openMenu = () => {
    setQuery("");
    setActiveIndex(Math.max(0, availableModels.findIndex((model) => model.id === value)));
    setOpen(true);
  };

  useLayoutEffect(() => {
    if (!open) return;
    const updatePosition = () => {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      const gap = 12;
      const width = Math.min(Math.max(rect.width, 380), window.innerWidth - gap * 2);
      const below = window.innerHeight - rect.bottom - gap - 8;
      const above = rect.top - gap - 8;
      const opensUp = below < 300 && above > below;
      setPosition({
        width,
        left: Math.max(gap, Math.min(rect.right - width, window.innerWidth - width - gap)),
        maxHeight: Math.min(520, Math.max(0, opensUp ? above : below)),
        ...(opensUp ? { bottom: window.innerHeight - rect.top + 8 } : { top: rect.bottom + 8 }),
      });
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open]);

  useEffect(() => {
    if (open && positioned) searchRef.current?.focus({ preventScroll: true });
  }, [open, positioned]);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  useEffect(() => {
    if (open) document.getElementById(`${listboxId}-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex, listboxId]);

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "Tab") {
      close(true);
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && filteredModels.length) {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((index) => (index + direction + filteredModels.length) % filteredModels.length);
    } else if (event.key === "Enter" && event.target === searchRef.current) {
      event.preventDefault();
      const model = filteredModels[activeIndex];
      if (model) choose(model);
    }
  };

  return (
    <div className={`model-picker ${open ? "is-open" : ""}`} ref={rootRef}>
      <input
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        spellCheck={false}
      />
      <button
        ref={triggerRef}
        type="button"
        className="model-picker__trigger"
        aria-label={`${label}：选择模型`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            openMenu();
          } else if (event.key === "Escape") close();
        }}
      >
        选择模型<ChevronDown size={14} />
      </button>
      {open && position ? createPortal(
        <div
          ref={menuRef}
          id={menuId}
          className="model-picker__menu"
          role="dialog"
          aria-label={`${label}选择`}
          style={position}
          onKeyDown={onMenuKeyDown}
        >
          <header className="model-picker__header">
            <div><strong>{label}</strong></div>
            <span>{availableModels.length} 个可用</span>
            <button
              type="button"
              className="model-picker__refresh"
              aria-label="刷新模型列表"
              title="重新读取上游可用模型"
              disabled={loading || !onRefresh}
              onClick={onRefresh}
            ><RefreshCw size={15} className={loading ? "spin" : ""} /></button>
          </header>
          <div className="model-picker__search">
            <Search size={17} />
            <input
              ref={searchRef}
              role="combobox"
              aria-label={`搜索${label}`}
              aria-autocomplete="list"
              aria-expanded="true"
              aria-controls={listboxId}
              aria-activedescendant={filteredModels[activeIndex] ? `${listboxId}-${activeIndex}` : undefined}
              placeholder="搜索模型…"
              value={query}
              autoComplete="off"
              onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }}
            />
          </div>
          {loading ? <p className="model-picker__message" role="status">正在读取上游模型列表…</p> : error ? (
            <p className="model-picker__message is-error" role="alert">{error}</p>
          ) : null}
          <div className="model-picker__list" role="listbox" id={listboxId} aria-label={`${label}可选列表`} aria-busy={loading}>
            {!loading && !error ? filteredModels.map((model, index) => (
              <button
                key={model.id}
                id={`${listboxId}-${index}`}
                type="button"
                role="option"
                tabIndex={-1}
                aria-selected={model.id === value}
                className={`${model.id === value ? "is-selected" : ""} ${index === activeIndex ? "is-active" : ""}`}
                onPointerEnter={() => setActiveIndex(index)}
                onClick={() => choose(model)}
              >
                <span><strong>{model.id}</strong><small>{model.provider_id}</small></span>
                {model.id === value ? <Check size={16} /> : null}
              </button>
            )) : null}
            {!loading && !error && !filteredModels.length ? (
              <p className="model-picker__message">
                {keyword ? "没有找到匹配的模型。" : `上游未提供可用的${label}，可手动填写模型 ID。`}
              </p>
            ) : null}
          </div>
        </div>,
        document.body,
      ) : null}
    </div>
  );
}
