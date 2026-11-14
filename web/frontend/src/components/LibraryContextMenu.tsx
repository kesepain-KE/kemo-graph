import { useLayoutEffect, useRef, useState, useEffect, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { LucideIcon } from "lucide-react";

export type LibraryMenuItem = {
  label: string;
  icon: LucideIcon;
  action: () => void;
  disabled?: boolean;
  shortcut?: string;
  danger?: boolean;
  title?: string;
};

export function LibraryContextMenu({ x, y, groups, onClose, onShortcut }: {
  x: number;
  y: number;
  groups: LibraryMenuItem[][];
  onClose: (restoreFocus?: boolean) => void;
  onShortcut: (event: KeyboardEvent) => void;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: x, top: y });
  useLayoutEffect(() => {
    const element = menu.current!;
    const bounds = element.getBoundingClientRect();
    setPosition({
      left: Math.max(8, Math.min(x, window.innerWidth - bounds.width - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - bounds.height - 8)),
    });
    element.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }, [x, y]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node)) onClose();
    };
    const scrolled = (event: Event) => {
      if (!menu.current?.contains(event.target as Node)) onClose();
    };
    const resized = () => onClose();
    document.addEventListener("pointerdown", outside);
    document.addEventListener("scroll", scrolled, true);
    window.addEventListener("resize", resized);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", resized);
    };
  }, [onClose]);

  return createPortal(<div ref={menu} className="library-context-menu" role="menu"
    aria-label="文件操作" style={position} onContextMenu={(event) => event.preventDefault()}
    onKeyDown={(event) => {
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); onClose(true); return;
      }
      const buttons = Array.from(menu.current!.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      if (["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
          : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next]?.focus();
      } else {
        onShortcut(event);
      }
    }}>
    {groups.filter((group) => group.length).map((group, index) => <div role="group" key={index}>
      {group.map(({ label, icon: Icon, action, disabled, shortcut, danger, title }) => <button
        type="button" role="menuitem" key={label} disabled={disabled} title={title}
        aria-label={label} aria-keyshortcuts={shortcut?.replace("Ctrl", "Control")}
        className={danger ? "is-danger" : undefined}
        onClick={() => { onClose(true); action(); }}>
        <Icon size={15} aria-hidden="true" /><span>{label}</span>{shortcut && <kbd>{shortcut}</kbd>}
      </button>)}
    </div>)}
  </div>, document.body);
}
