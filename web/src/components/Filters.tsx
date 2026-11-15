import { useState } from "react";
import type { Value } from "../data";
import { usePopover } from "./Popover";

export function MultiFilter({ label, options, value, onChange }: {
  label: string; options: Value[]; value: Value[]; onChange: (v: Value[]) => void;
}) {
  const { open, setOpen, close, ref } = usePopover();
  const [find, setFind] = useState("");
  const set = new Set(value);
  const shown = find ? options.filter((o) => String(o).includes(find)) : options;
  const summary = value.length === 0 ? "" : value.length <= 2 ? value.join(", ") : `${value.slice(0, 2).join(", ")} 他${value.length - 2}`;

  return (
    <div className="pop-anchor" ref={ref}>
      <span className={"chip" + (value.length ? " active" : "")}>
        <button className="chip-main" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {label}
          {summary ? <b>: {summary}</b> : <span className="caret">▾</span>}
        </button>
        {value.length > 0 && (
          <button className="chip-x" onClick={() => onChange([])} aria-label={`${label}の絞り込みを解除`}>×</button>
        )}
      </span>
      {open && (
        <div className="popover">
          {options.length > 10 && (
            <input className="pop-find" autoFocus placeholder="絞り込む…" value={find} onChange={(e) => setFind(e.target.value)} />
          )}
          <div className="pop-list">
            {shown.map((o) => (
              <label key={String(o)} className="pop-item">
                <input
                  type="checkbox"
                  checked={set.has(o)}
                  onChange={() => onChange(set.has(o) ? value.filter((x) => x !== o) : [...value, o])}
                />
                {String(o)}
              </label>
            ))}
          </div>
          <div className="pop-foot">
            <button className="link" onClick={() => onChange([])}>解除</button>
            <button className="link" onClick={close}>閉じる</button>
          </div>
        </div>
      )}
    </div>
  );
}

export function YearFilter({ bounds, value, onChange }: {
  bounds: [number, number]; value: [number, number] | null; onChange: (v: [number, number] | null) => void;
}) {
  const { open, setOpen, close, ref } = usePopover();
  const [lo, hi] = value ?? bounds;
  const years = Array.from({ length: bounds[1] - bounds[0] + 1 }, (_, i) => bounds[0] + i);
  const set = (a: number, b: number) =>
    onChange(a === bounds[0] && b === bounds[1] ? null : [Math.min(a, b), Math.max(a, b)]);

  return (
    <div className="pop-anchor" ref={ref}>
      <span className={"chip" + (value ? " active" : "")}>
        <button className="chip-main" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          年{value ? <b>: {lo}–{hi}</b> : <span className="caret">▾</span>}
        </button>
        {value && <button className="chip-x" onClick={() => onChange(null)} aria-label="年の絞り込みを解除">×</button>}
      </span>
      {open && (
        <div className="popover year-pop">
          <div className="year-row">
            <select value={lo} onChange={(e) => set(+e.target.value, hi)} aria-label="開始年">
              {years.map((y) => <option key={y}>{y}</option>)}
            </select>
            <span>–</span>
            <select value={hi} onChange={(e) => set(lo, +e.target.value)} aria-label="終了年">
              {years.map((y) => <option key={y}>{y}</option>)}
            </select>
          </div>
          <div className="year-grid">
            {years.map((y) => (
              <button key={y} className={y >= lo && y <= hi ? "in" : ""} onClick={() => set(y, y)}>
                {y}
              </button>
            ))}
          </div>
          <div className="pop-foot">
            <button className="link" onClick={() => onChange(null)}>全期間</button>
            <button className="link" onClick={close}>閉じる</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 集計の軸を選ぶ（選んだ順に番号が付く）。 */
export function AxisPicker({ options, labels, value, onChange, max }: {
  options: string[]; labels: Record<string, string>; value: string[]; onChange: (v: string[]) => void; max?: number;
}) {
  return (
    <div className="axis-picker" role="group" aria-label="集計の軸">
      {options.map((c) => {
        const i = value.indexOf(c);
        const on = i >= 0;
        const full = !on && max !== undefined && value.length >= max;
        return (
          <button
            key={c}
            className={"chip axis" + (on ? " active" : "")}
            disabled={full}
            title={c}
            onClick={() => onChange(on ? value.filter((x) => x !== c) : [...value, c])}
          >
            {on && <span className="axis-n">{i + 1}</span>}
            {labels[c] ?? c}
          </button>
        );
      })}
    </div>
  );
}
