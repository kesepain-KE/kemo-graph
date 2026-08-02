import { Check, ChevronDown } from "lucide-react";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";

export type ThemedSelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

type MenuPosition = {
  left: number;
  width: number;
  maxHeight: number;
  top?: number;
  bottom?: number;
};

type ThemedSelectProps = {
  ariaLabel: string;
  value: string;
  options: ThemedSelectOption[];
  onChange: (value: string) => void;
  className?: string;
  disabled?: boolean;
  placeholder?: string;
};

export function ThemedSelect({
  ariaLabel,
  value,
  options,
  onChange,
  className = "",
  disabled = false,
  placeholder = "请选择",
}: ThemedSelectProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();
  const selectedIndex = options.findIndex((option) => option.value === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  const findEnabled = (start: number, direction: -1 | 1) => {
    if (!options.length) return -1;
    let index = start;
    for (let count = 0; count < options.length; count += 1) {
      index = (index + direction + options.length) % options.length;
      if (!options[index].disabled) return index;
    }
    return -1;
  };

  const openMenu = () => {
    if (disabled || !options.length) return;
    const initialIndex = selectedIndex >= 0 && !options[selectedIndex]?.disabled
      ? selectedIndex
      : findEnabled(-1, 1);
    setActiveIndex(Math.max(0, initialIndex));
    setOpen(true);
  };

  const updatePosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const viewportGap = 12;
    const menuGap = 6;
    const width = Math.min(Math.max(rect.width, 180), window.innerWidth - viewportGap * 2);
    const left = Math.min(
      Math.max(viewportGap, rect.right - width),
      window.innerWidth - width - viewportGap,
    );
    const below = window.innerHeight - rect.bottom - viewportGap - menuGap;
    const above = rect.top - viewportGap - menuGap;
    const opensUp = below < 180 && above > below;
    const available = Math.max(96, opensUp ? above : below);
    const maxHeight = Math.min(280, available);
    setMenuPosition({
      left,
      width,
      maxHeight,
      ...(opensUp
        ? { bottom: window.innerHeight - rect.top + menuGap }
        : { top: rect.bottom + menuGap }),
    });
  };

  useLayoutEffect(() => {
    if (!open) {
      setMenuPosition(null);
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
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutside);
    return () => document.removeEventListener("pointerdown", closeOnOutside);
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const selectAt = (index: number) => {
    const option = options[index];
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openMenu();
        return;
      }
      const next = findEnabled(activeIndex, event.key === "ArrowDown" ? 1 : -1);
      if (next >= 0) setActiveIndex(next);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      if (!open) return;
      event.preventDefault();
      const next = findEnabled(event.key === "Home" ? -1 : 0, event.key === "Home" ? 1 : -1);
      if (next >= 0) setActiveIndex(next);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) selectAt(activeIndex);
      else openMenu();
    }
  };

  const menuStyle = menuPosition ? ({
    left: menuPosition.left,
    width: menuPosition.width,
    maxHeight: menuPosition.maxHeight,
    top: menuPosition.top,
    bottom: menuPosition.bottom,
  } satisfies CSSProperties) : undefined;

  return (
    <div className={`themed-select ${open ? "is-open" : ""} ${className}`.trim()} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="themed-select__trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open ? `${listboxId}-${activeIndex}` : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onTriggerKeyDown}
      >
        <span>{selected?.label ?? placeholder}</span>
        <ChevronDown size={15} />
      </button>
      {open && menuPosition ? createPortal(
        <div
          id={listboxId}
          ref={menuRef}
          className="themed-select__menu"
          role="listbox"
          aria-label={ariaLabel}
          style={menuStyle}
        >
          {options.map((option, index) => (
            <button
              id={`${listboxId}-${index}`}
              type="button"
              role="option"
              aria-selected={option.value === value}
              disabled={option.disabled}
              className={`${option.value === value ? "is-selected" : ""} ${index === activeIndex ? "is-active" : ""}`.trim()}
              key={option.value}
              onPointerEnter={() => setActiveIndex(index)}
              onClick={() => selectAt(index)}
            >
              <span>{option.label}</span>
              {option.value === value ? <Check size={14} /> : null}
            </button>
          ))}
        </div>,
        document.body,
      ) : null}
    </div>
  );
}
