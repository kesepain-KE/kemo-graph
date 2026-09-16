import { CalendarDays, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";

// The panel is measured with a layout effect on the client; during SSR the
// renderer has no layout, so fall back to useEffect to avoid a hydration warning.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
const GRID_CELLS = 42;

const pad = (value: number) => String(value).padStart(2, "0");

type DayParts = { year: number; month: number; day: number };

type PanelPosition = {
  left: number;
  width: number;
  maxHeight: number;
  top?: number;
  bottom?: number;
};

type ThemedDatePickerProps = {
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
  disabled?: boolean;
  placeholder?: string;
};

const daysInMonth = (year: number, month: number) => new Date(Date.UTC(year, month + 1, 0)).getUTCDate();

const parseIso = (value: string): DayParts | null => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]) - 1;
  const day = Number(match[3]);
  if (month < 0 || month > 11) return null;
  if (day < 1 || day > daysInMonth(year, month)) return null;
  return { year, month, day };
};

const toIso = ({ year, month, day }: DayParts) => `${year}-${pad(month + 1)}-${pad(day)}`;

const utcToday = (): DayParts => {
  const now = new Date();
  return { year: now.getUTCFullYear(), month: now.getUTCMonth(), day: now.getUTCDate() };
};

// Resolved once per page load; only drives the "today" marker.
const todayIso = toIso(utcToday());

const addDays = (parts: DayParts, delta: number): DayParts => {
  const next = new Date(Date.UTC(parts.year, parts.month, parts.day + delta));
  return { year: next.getUTCFullYear(), month: next.getUTCMonth(), day: next.getUTCDate() };
};

const addMonths = (parts: DayParts, delta: number): DayParts => {
  const next = new Date(Date.UTC(parts.year, parts.month + delta, 1));
  const year = next.getUTCFullYear();
  const month = next.getUTCMonth();
  return { year, month, day: Math.min(parts.day, daysInMonth(year, month)) };
};

// Monday-first index, matching the rendered weekday header.
const weekdayIndex = (parts: DayParts) => (new Date(Date.UTC(parts.year, parts.month, parts.day)).getUTCDay() + 6) % 7;

const gridStart = (year: number, month: number) => {
  const first: DayParts = { year, month, day: 1 };
  return addDays(first, -weekdayIndex(first));
};

export function ThemedDatePicker({
  ariaLabel,
  value,
  onChange,
  className = "",
  disabled = false,
  placeholder = "选择日期",
}: ThemedDatePickerProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<PanelPosition | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const dayRefs = useRef(new Map<string, HTMLButtonElement>());
  const panelId = useId();

  const selected = parseIso(value);
  const today = utcToday();
  const [view, setView] = useState(() => {
    const base = selected ?? today;
    return { year: base.year, month: base.month };
  });
  const [focusIso, setFocusIso] = useState(() => toIso(selected ?? today));

  // focusIso is the single source of truth: the visible month always follows it,
  // so arrow keys, page keys and header buttons never diverge from the focus ring.
  const focusOn = (parts: DayParts) => {
    setFocusIso(toIso(parts));
    setView((current) => (
      current.year === parts.year && current.month === parts.month
        ? current
        : { year: parts.year, month: parts.month }
    ));
  };

  const openPanel = () => {
    if (disabled) return;
    const base = parseIso(value) ?? utcToday();
    setFocusIso(toIso(base));
    setView({ year: base.year, month: base.month });
    setOpen(true);
  };

  const updatePosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const viewportGap = 12;
    const panelGap = 6;
    const width = Math.min(Math.max(rect.width, 288), window.innerWidth - viewportGap * 2);
    const left = Math.min(Math.max(viewportGap, rect.left), window.innerWidth - width - viewportGap);
    const below = window.innerHeight - rect.bottom - viewportGap - panelGap;
    const above = rect.top - viewportGap - panelGap;
    const opensUp = below < 356 && above > below;
    const available = Math.max(190, opensUp ? above : below);
    setPosition({
      left,
      width,
      maxHeight: Math.min(360, available),
      ...(opensUp
        ? { bottom: window.innerHeight - rect.top + panelGap }
        : { top: rect.bottom + panelGap }),
    });
  };

  useIsoLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return undefined;
    }
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !panelRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutside);
    return () => document.removeEventListener("pointerdown", closeOnOutside);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    dayRefs.current.get(focusIso)?.focus({ preventScroll: true });
  }, [open, focusIso, view]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const closeAndRestore = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const select = (parts: DayParts) => {
    onChange(toIso(parts));
    closeAndRestore();
  };

  const onPanelKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const current = parseIso(focusIso) ?? utcToday();
    switch (event.key) {
      case "Escape": event.preventDefault(); closeAndRestore(); return;
      case "Tab": setOpen(false); return;
      case "Enter": case " ": event.preventDefault(); select(current); return;
      case "ArrowLeft": event.preventDefault(); focusOn(addDays(current, -1)); return;
      case "ArrowRight": event.preventDefault(); focusOn(addDays(current, 1)); return;
      case "ArrowUp": event.preventDefault(); focusOn(addDays(current, -7)); return;
      case "ArrowDown": event.preventDefault(); focusOn(addDays(current, 7)); return;
      case "Home": event.preventDefault(); focusOn(addDays(current, -weekdayIndex(current))); return;
      case "End": event.preventDefault(); focusOn(addDays(current, 6 - weekdayIndex(current))); return;
      case "PageUp": event.preventDefault(); focusOn(addMonths(current, -1)); return;
      case "PageDown": event.preventDefault(); focusOn(addMonths(current, 1)); return;
      default: return;
    }
  };

  const triggerText = selected
    ? `${selected.year}/${pad(selected.month + 1)}/${pad(selected.day)}`
    : placeholder;

  const start = gridStart(view.year, view.month);
  const cells = Array.from({ length: GRID_CELLS }, (_, index) => {
    const parts = addDays(start, index);
    return {
      iso: toIso(parts),
      day: parts.day,
      inMonth: parts.year === view.year && parts.month === view.month,
    };
  });

  const panelStyle = position ? ({
    left: position.left,
    width: position.width,
    maxHeight: position.maxHeight,
    top: position.top,
    bottom: position.bottom,
  } satisfies CSSProperties) : undefined;

  return (
    <div className={`themed-date ${open ? "is-open" : ""} ${className}`.trim()} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`themed-date__trigger ${selected ? "" : "is-empty"}`.trim()}
        aria-label={ariaLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openPanel())}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            if (open) {
              event.preventDefault();
              setOpen(false);
            }
            return;
          }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) openPanel();
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (open) setOpen(false);
            else openPanel();
          }
        }}
      >
        <span>{triggerText}</span>
        <CalendarDays size={15} />
      </button>
      {open && position ? createPortal(
        <div
          id={panelId}
          ref={panelRef}
          className="themed-date__panel"
          role="dialog"
          aria-label={ariaLabel}
          style={panelStyle}
          onKeyDown={onPanelKeyDown}
        >
          <div className="themed-date__header">
            <button
              type="button"
              aria-label="上一年"
              onClick={() => focusOn(addMonths(parseIso(focusIso) ?? utcToday(), -12))}
            >
              <ChevronsLeft size={15} />
            </button>
            <button
              type="button"
              aria-label="上个月"
              onClick={() => focusOn(addMonths(parseIso(focusIso) ?? utcToday(), -1))}
            >
              <ChevronLeft size={15} />
            </button>
            <strong className="themed-date__title">{view.year}年{pad(view.month + 1)}月</strong>
            <button
              type="button"
              aria-label="下个月"
              onClick={() => focusOn(addMonths(parseIso(focusIso) ?? utcToday(), 1))}
            >
              <ChevronRight size={15} />
            </button>
            <button
              type="button"
              aria-label="下一年"
              onClick={() => focusOn(addMonths(parseIso(focusIso) ?? utcToday(), 12))}
            >
              <ChevronsRight size={15} />
            </button>
          </div>
          <div className="themed-date__weekdays" aria-hidden="true">
            {WEEKDAYS.map((label) => <span key={label}>{label}</span>)}
          </div>
          <div className="themed-date__grid" role="group" aria-label="日期">
            {cells.map((cell) => (
              <button
                key={cell.iso}
                ref={(node) => {
                  if (node) dayRefs.current.set(cell.iso, node);
                  else dayRefs.current.delete(cell.iso);
                }}
                type="button"
                tabIndex={cell.iso === focusIso ? 0 : -1}
                aria-current={cell.iso === value ? "date" : undefined}
                aria-label={cell.iso}
                className={[
                  "themed-date__day",
                  cell.inMonth ? "" : "is-outside",
                  cell.iso === todayIso ? "is-today" : "",
                  cell.iso === value ? "is-selected" : "",
                ].filter(Boolean).join(" ")}
                onClick={() => select(parseIso(cell.iso) ?? utcToday())}
                onPointerEnter={() => setFocusIso(cell.iso)}
              >
                {cell.day}
              </button>
            ))}
          </div>
          <div className="themed-date__footer">
            <button
              type="button"
              disabled={!selected}
              onClick={() => { onChange(""); closeAndRestore(); }}
            >
              清除
            </button>
            <button type="button" onClick={() => select(utcToday())}>今天</button>
          </div>
        </div>,
        // Keep the panel inside its owning dialog: a modal dialog's top layer
        // would otherwise hide a body-level portal.
        rootRef.current?.closest("dialog") ?? document.body,
      ) : null}
    </div>
  );
}

