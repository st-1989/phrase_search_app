import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { PREV_RATING_COLUMN, RATING_CHANGE_COLUMN, SOURCE_COLUMN, YEAR_COLUMN, pieces, spansOf, type Table } from "../data";

export type ResultListHandle = { focusFirst: () => void };

/** 文の一覧（見えている行だけ描画）。↑↓/j k で移動、Enter で全文、Esc で onExit。 */
export const ResultList = forwardRef<ResultListHandle, {
  table: Table;
  rows: Int32Array | number[];
  terms: string[];
  excl: string[];
  context: number;
  dim?: boolean;
  onExit?: () => void;
  className?: string;
}>(function ResultList({ table, rows, terms, excl, context, dim, onExit, className }, ref) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState<Set<number>>(new Set());

  useEffect(() => {
    setActive(-1);
    setOpen(new Set());
    parentRef.current?.scrollTo({ top: 0 });
  }, [rows]);

  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 92,
    overscan: 8,
  });

  const focusRow = useCallback((i: number) => {
    setActive(i);
    v.scrollToIndex(i, { align: "auto" });
    parentRef.current?.focus({ preventScroll: true });
  }, [v]);

  useImperativeHandle(ref, () => ({ focusFirst: () => focusRow(0) }), [focusRow]);

  const toggle = (i: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "j") {
      e.preventDefault();
      focusRow(Math.min(rows.length - 1, active + 1));
    } else if (e.key === "ArrowUp" || e.key === "k") {
      e.preventDefault();
      if (active <= 0) {
        setActive(-1);
        onExit?.();
      } else focusRow(active - 1);
    } else if ((e.key === "Enter" || e.key === " ") && active >= 0) {
      e.preventDefault();
      toggle(active);
    } else if (e.key === "Escape") {
      setActive(-1);
      onExit?.();
    }
  };

  return (
    <div
      ref={parentRef}
      className={"results" + (dim ? " stale" : "") + (className ? " " + className : "")}
      tabIndex={0}
      onKeyDown={onKey}
      aria-label="文の一覧"
    >
      <div style={{ height: v.getTotalSize(), position: "relative" }}>
        {v.getVirtualItems().map((it) => {
          const row = rows[it.index];
          const isOpen = open.has(it.index);
          return (
            <article
              key={it.key}
              data-index={it.index}
              ref={v.measureElement}
              className={"card" + (it.index === active ? " active" : "") + (isOpen ? " open" : "")}
              style={{ transform: `translateY(${it.start}px)` }}
              onClick={() => {
                setActive(it.index);
                toggle(it.index);
              }}
            >
              <Meta table={table} row={row} />
              <Snippet text={table.text[row]} terms={terms} excl={excl} context={context} full={isOpen} />
            </article>
          );
        })}
      </div>
    </div>
  );
});

function Meta({ table, row }: { table: Table; row: number }) {
  const g = (c: string) => {
    const x = table.value(c, row);
    return x === null || x === "" ? "" : String(x);
  };
  const change = g(RATING_CHANGE_COLUMN);
  const moved = change === "引き上げ" || change === "引き下げ";
  // 格付が動いたレポートは「前回 → 今回」で示す
  const now = moved ? `${g(PREV_RATING_COLUMN)} → ${g("発行体格付(業務)")}` : g("発行体格付(業務)");
  const rating = [now, g("格付の方向性(業務)")].filter(Boolean).join(" ");
  const rest = [g(YEAR_COLUMN), g("公表日(CDEX)"), g("業種(業務)"), g("格付アクション(業務)"), g("担当アナリスト(CDEX)"), g(SOURCE_COLUMN)]
    .filter(Boolean);
  return (
    <div className="meta">
      <span className="issuer">{g("発行体名(CDEX)")}</span>
      {rating && <span className={"rating" + (moved ? (change === "引き上げ" ? " up" : " down") : "")}>{rating}</span>}
      <span className="meta-rest">{rest.join(" · ")}</span>
    </div>
  );
}

function Snippet({ text, terms, excl, context, full }: {
  text: string; terms: string[]; excl: string[]; context: number; full: boolean;
}) {
  const spans = useMemo(() => spansOf(text, terms, excl), [text, terms, excl]);
  const p = pieces(text, spans, context, full);
  return (
    <p className={"snippet" + (full ? " full" : "")}>
      {p.before && <span className="ctx">…</span>}
      {p.pieces.map((x, i) =>
        x.term < 0 ? <span key={i} className="ctx">{x.s}</span> : <mark key={i} className={"t" + x.term}>{x.s}</mark>,
      )}
      {p.after && <span className="ctx">…</span>}
    </p>
  );
}
