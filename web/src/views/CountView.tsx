import { useMemo } from "react";
import { GROUP_COLUMNS, GROUP_LABELS, type Table } from "../data";
import { COUNT_SORTS, METRICS, aggregate, sortRows, toCsv } from "../stats";
import { AxisPicker } from "../components/Filters";
import { BarChart, Heatmap } from "../components/Charts";
import { Control, Segmented } from "../components/Popover";
import type { AnalysisSettings } from "./settings";

const pct = (v: number) => (Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "–");
const f1 = (v: number, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : "–");

export function CountView({ table, mask, hits, s, set, fontSize, dim }: {
  table: Table; mask: Uint8Array | null; hits: Int32Array;
  s: AnalysisSettings; set: (p: Partial<AnalysisSettings>) => void; fontSize: number; dim: boolean;
}) {
  const options = GROUP_COLUMNS.filter((c) => table.has(c));
  const cols = s.groupCols.filter((c) => table.has(c));
  const agg = useMemo(
    () => (cols.length ? aggregate(table, mask, hits, s.unit, cols) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [table, mask, hits, s.unit, cols.join("\u0000")],
  );
  const rows = useMemo(
    () => (agg ? sortRows(agg.rows.filter((r) => r.n >= s.minDocs), s.countSort) : []),
    [agg, s.minDocs, s.countSort],
  );
  const unitName = agg?.isReport ? "レポート" : "文";
  const labels = cols.map((c) => GROUP_LABELS[c] ?? c);

  const download = () => {
    const blob = new Blob([toCsv(cols, rows)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "件数の集計.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className={"pane" + (dim ? " stale" : "")}>
      <div className="controls">
        <Control label="軸（選んだ順）">
          <AxisPicker options={options} labels={GROUP_LABELS} value={cols} onChange={(v) => set({ groupCols: v })} />
        </Control>
        <UnitControl table={table} s={s} set={set} />
        <Control label="グラフ">
          <select value={s.metric} onChange={(e) => set({ metric: e.target.value as AnalysisSettings["metric"] })}>
            {METRICS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        </Control>
        <Control label="並び順">
          <select value={s.countSort} onChange={(e) => set({ countSort: e.target.value as AnalysisSettings["countSort"] })}>
            {COUNT_SORTS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        </Control>
        <Control label="最小の全文書数" hint="これより少ない組み合わせは表示しません（率が不安定なため）">
          <input type="number" min={1} value={s.minDocs} className="num-input"
            onChange={(e) => set({ minDocs: Math.max(1, +e.target.value || 1) })} />
        </Control>
      </div>

      {!agg ? (
        <p className="empty">集計の軸を1つ以上選んでください。</p>
      ) : (
        <>
          <p className="caption">
            全体の一致率 <b>{pct(agg.p0)}</b>（{agg.nHit.toLocaleString()} / {agg.nUnits.toLocaleString()} {unitName}）
          </p>
          {rows.length === 0 ? (
            <p className="empty">条件に合う組み合わせがありません。</p>
          ) : (
            <>
              <div className="chart-scroll">
                {cols.length === 1 && (
                  <BarChart rows={rows} metric={s.metric} p0={agg.p0} xLabel={labels[0]} fontSize={fontSize} />
                )}
                {cols.length === 2 && (
                  <Heatmap rows={rows} metric={s.metric} cols={[cols[0], cols[1]]} labels={[labels[0], labels[1]]} fontSize={fontSize} />
                )}
                {cols.length > 2 && <p className="caption">3軸以上は表のみで表示します。</p>}
              </div>
              <div className="table-head">
                <span className="muted">{rows.length.toLocaleString()} 組</span>
                <button className="link" onClick={download}>CSV で保存</button>
              </div>
              <div className="table-scroll">
                <table className="data">
                  <thead>
                    <tr>
                      {labels.map((l) => <th key={l} className="txt">{l}</th>)}
                      <th>一致件数</th><th>全文書数</th><th>一致率</th><th>95%区間</th>
                      <th>全体比</th><th>期待件数</th><th>標準化残差</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.label}>
                        {r.key.map((k, i) => <td key={i} className="txt">{String(k)}</td>)}
                        <td>{r.k.toLocaleString()}</td>
                        <td>{r.n.toLocaleString()}</td>
                        <td>
                          <span className="rate-cell">
                            <span className="rate-bar" style={{ width: `${Math.min(100, r.rate * 100)}%` }} />
                            {pct(r.rate)}
                          </span>
                        </td>
                        <td className="muted">{pct(r.lo)}–{pct(r.hi)}</td>
                        <td>{f1(r.ratio, 2)}</td>
                        <td>{f1(r.expected)}</td>
                        <td className={Math.abs(r.resid) > 2 ? "strong" : ""}>{f1(r.resid)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="note">
                95%区間は Wilson の信頼区間（件数が少ないほど広い）。全体比 = 一致率 ÷ 全体の一致率。
                期待件数 = 全文書数 × 全体の一致率。標準化残差 = (一致件数 − 期待件数) ÷ √(全文書数 × 全体率 × (1 − 全体率))。
                絶対値が 2 を超えると偶然とは考えにくい偏りの目安で、太字で示します。
                棒グラフの破線は全体の一致率（全体比では 1、残差では ±2 と 0）、縦線は信頼区間です。
              </p>
            </>
          )}
        </>
      )}
    </div>
  );
}

export function UnitControl({ table, s, set }: {
  table: Table; s: AnalysisSettings; set: (p: Partial<AnalysisSettings>) => void;
}) {
  if (!table.reportId) return null;
  return (
    <Control label="単位" hint="文: 1行ごと / レポート: 発行体IDと公表日が同じ行を1件にまとめ、1行でも一致すれば一致">
      <Segmented
        label="集計の単位"
        value={s.unit}
        onChange={(unit) => set({ unit })}
        options={[{ id: "row", label: "文" }, { id: "report", label: "レポート" }]}
      />
    </Control>
  );
}
