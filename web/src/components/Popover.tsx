import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/** 外側クリック・Esc で閉じる小さなポップオーバー。 */
export function usePopover() {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return { open, setOpen, close, ref };
}

export function Segmented<T extends string | number>({ value, options, onChange, label, small }: {
  value: T; options: { id: T; label: ReactNode; hint?: string }[]; onChange: (v: T) => void; label: string; small?: boolean;
}) {
  return (
    <div className={"segmented" + (small ? " small" : "")} role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.id)}
          role="radio"
          aria-checked={value === o.id}
          className={value === o.id ? "on" : ""}
          title={o.hint}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** ラベル付きの小さな設定項目（分析ビューの上の操作行で使う）。 */
export function Control({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="control" title={hint}>
      <span className="control-label">{label}</span>
      {children}
    </label>
  );
}
