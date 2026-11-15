import { useState, type ReactNode } from "react";
import { compareValues, type Value } from "../data";
import { METRICS, type AggRow, type Metric } from "../stats";

// 図の決まり: タイトルなし・数値の目盛りは回転しない・目盛りは内向き・セルには全て数値を表示

const SEQ = ["#eef1fd", "#9fb0ef", "#3554c8", "#16246b"]; // 1色の濃淡（量）
const DIV = ["#b4501a", "#e7a77c", "#e4e4e4", "#8fa3ec", "#2a45b8"]; // 少ない ← 灰 → 多い

function hex(c: string): [number, number, number] {
  const n = parseInt(c.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function lerpStops(stops: string[], t: number): [number, number, number] {
  const x = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const a = hex(stops[i]);
  const b = hex(stops[i + 1]);
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f)) as [number, number, number];
}
const rgb = (c: [number, number, number]) => `rgb(${c[0]},${c[1]},${c[2]})`;
const isDark = (c: [number, number, number]) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] < 140;

/** 全角は文字サイズ分、半角は約 0.55 倍の幅とみなす。 */
export function textWidth(s: string, size: number) {
  let w = 0;
  for (const ch of s) w += ch.charCodeAt(0) > 255 ? size : size * 0.58;
  return w;
}

function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  const end = Math.ceil(hi / step - 1e-9) * step; // 最大値を含む次の目盛りまで
  for (let v = Math.floor(lo / step + 1e-9) * step; v <= end + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

function metricValue(r: AggRow, m: Metric): number {
  return m === "k" ? r.k : m === "n" ? r.n : m === "rate" ? r.rate : m === "ratio" ? r.ratio : r.resid;
}

/** ツールチップ: 位置は外側の要素基準で追い、中身はマークに入ったときに差し替える。 */
function useTip() {
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const [body, setBody] = useState<ReactNode>(null);
  const wrapProps = {
    onMouseMove: (e: React.MouseEvent<HTMLDivElement>) => {
      const b = e.currentTarget.getBoundingClientRect();
      setPos({ x: e.clientX - b.left + e.currentTarget.scrollLeft + 14, y: e.clientY - b.top + 12 });
    },
    onMouseLeave: () => setBody(null),
  };
  const box = body ? <div className="tip" style={{ left: pos.x, top: pos.y }}>{body}</div> : null;
  return { setBody, wrapProps, box };
}

function tipBody(label: string, r: AggRow) {
  return (
    <>
      <b>{label}</b>
      <div>一致 {r.k.toLocaleString()} / {r.n.toLocaleString()}（{(r.rate * 100).toFixed(1)}%）</div>
      <div className="muted">95%区間 {(r.lo * 100).toFixed(1)}–{(r.hi * 100).toFixed(1)}% · 全体比 {r.ratio.toFixed(2)} · 残差 {r.resid.toFixed(1)}</div>
    </>
  );
}

export function BarChart({ rows, metric, p0, xLabel, fontSize }: {
  rows: AggRow[]; metric: Metric; p0: number; xLabel: string; fontSize: number;
}) {
  const tip = useTip();
  const m = METRICS.find((x) => x.id === metric)!;
  const fs = fontSize * 0.8;
  const vals = rows.map((r) => metricValue(r, metric)).map((v) => (Number.isFinite(v) ? v : 0));
  let lo = Math.min(0, ...vals);
  let hi = Math.max(0, ...vals);
  if (metric === "rate") hi = Math.max(hi, ...rows.map((r) => r.hi), p0);
  if (metric === "resid") { lo = Math.min(lo, -2.5); hi = Math.max(hi, 2.5); }
  if (metric === "ratio") hi = Math.max(hi, 1.2);
  const ticks = niceTicks(lo, hi);
  lo = Math.min(lo, ticks[0]);
  hi = Math.max(hi, ticks[ticks.length - 1]);

  const step = Math.max(44, Math.max(...rows.map((r) => textWidth(r.label, fs))) + 18);
  const tickW = Math.max(...ticks.map((t) => textWidth(m.fmt(t), fs)));
  const ml = tickW + fs * 3; // 左余白（目盛り＋軸ラベル）
  const mt = 12;
  const plotH = 300;
  const mb = fs * 3.6;
  const W = ml + step * rows.length + 16;
  const H = mt + plotH + mb;
  const y = (v: number) => mt + plotH - ((v - lo) / (hi - lo)) * plotH;
  const bw = Math.min(step * 0.6, 56);
  const refs = metric === "rate" ? [p0] : metric === "ratio" ? [1] : metric === "resid" ? [-2, 0, 2] : [];
  const x0 = ml;
  const x1 = ml + step * rows.length;

  return (
    <div className="chart-wrap" {...tip.wrapProps}>
      <svg width={W} height={H} className="chart" style={{ fontSize: fs }} role="img"
        aria-label={`${xLabel}ごとの${m.label}`}>
        {/* 目盛り（内向き・左右両側） */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x0} x2={x0 + 6} y1={y(t)} y2={y(t)} className="axis" />
            <line x1={x1 - 6} x2={x1} y1={y(t)} y2={y(t)} className="axis" />
            <text x={x0 - 6} y={y(t)} dy="0.35em" textAnchor="end" className="tick">{m.fmt(t)}</text>
          </g>
        ))}
        <rect x={x0} y={mt} width={x1 - x0} height={plotH} className="frame" />
        {refs.map((r) => (
          <line key={r} x1={x0} x2={x1} y1={y(r)} y2={y(r)} className="ref" />
        ))}
        {rows.map((r, i) => {
          const v = vals[i];
          const cx = ml + step * i + step / 2;
          const top = y(Math.max(v, 0));
          const h = Math.abs(y(v) - y(0));
          return (
            <g key={r.label}
              onMouseEnter={() => tip.setBody(tipBody(r.label, r))}>
              <rect x={cx - step / 2} y={mt} width={step} height={plotH} fill="transparent" />
              <path d={roundedBar(cx - bw / 2, top, bw, h, v >= 0)} className="bar" />
              {metric === "rate" && (
                <line x1={cx} x2={cx} y1={y(r.lo)} y2={y(r.hi)} className="ci" />
              )}
              <line x1={cx} x2={cx} y1={mt + plotH} y2={mt + plotH - 6} className="axis" />
              <text x={cx} y={mt + plotH + fs * 1.3} textAnchor="middle" className="tick">{r.label}</text>
            </g>
          );
        })}
        <text x={(x0 + x1) / 2} y={H - fs * 0.6} textAnchor="middle" className="axis-label">{xLabel}</text>
        <text transform={`translate(${fs * 0.9},${mt + plotH / 2}) rotate(-90)`} textAnchor="middle" className="axis-label">
          {m.label}
        </text>
      </svg>
      {tip.box}
    </div>
  );
}

function roundedBar(x: number, y: number, w: number, h: number, up: boolean) {
  const r = Math.min(4, h, w / 2);
  if (h <= 0) return "";
  if (up) {
    return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
  }
  return `M${x},${y} V${y + h - r} Q${x},${y + h} ${x + r},${y + h} H${x + w - r} Q${x + w},${y + h} ${x + w},${y + h - r} V${y} Z`;
}

export function Heatmap({ rows, metric, cols, labels, fontSize }: {
  rows: AggRow[]; metric: Metric; cols: [string, string]; labels: [string, string]; fontSize: number;
}) {
  const tip = useTip();
  const m = METRICS.find((x) => x.id === metric)!;
  const fs = fontSize * 0.8;
  const [yc, xc] = cols;
  const uniq = (i: number, c: string) => {
    const seen = new Map<string, Value>();
    for (const r of rows) seen.set(String(r.key[i]), r.key[i]);
    return [...seen.values()].sort((a, b) => compareValues(c, a, b)).map(String);
  };
  const ys = uniq(0, yc);
  const xs = uniq(1, xc);
  const cell = new Map(rows.map((r) => [`${r.key[0]}\u0000${r.key[1]}`, r]));

  const vals = rows.map((r) => metricValue(r, metric)).filter(Number.isFinite);
  const transform = (v: number) => (metric === "ratio" ? Math.log2(Math.max(v, 1e-6)) : v);
  const mid = m.mid === null ? null : transform(m.mid);
  const tv = vals.map(transform);
  const vmin = Math.min(...tv);
  const vmax = Math.max(...tv);
  const dev = mid === null ? 0 : Math.max(...tv.map((v) => Math.abs(v - mid))) || 1;
  const color = (v: number) => {
    const t = transform(v);
    return mid === null ? lerpStops(SEQ, vmax > vmin ? (t - vmin) / (vmax - vmin) : 0.5) : lerpStops(DIV, 0.5 + (t - mid) / (2 * dev));
  };

  const cw = Math.max(56, Math.max(...xs.map((x) => textWidth(x, fs)), textWidth(m.fmt(-88.8), fs)) + 18);
  const ch = Math.round(fs * 2.2);
  const ml = Math.max(...ys.map((v) => textWidth(v, fs))) + fs * 2.8;
  const mt = 8;
  const mb = fs * 3.6;
  const W = ml + cw * xs.length + 8;
  const H = mt + ch * ys.length + mb;

  const legendStops = mid === null ? SEQ : DIV;
  const legendLo = mid === null ? m.fmt(Math.min(...vals)) : metric === "ratio" ? m.fmt(2 ** (mid - dev)) : m.fmt(mid - dev);
  const legendHi = mid === null ? m.fmt(Math.max(...vals)) : metric === "ratio" ? m.fmt(2 ** (mid + dev)) : m.fmt(mid + dev);

  return (
    <div className="chart-wrap" {...tip.wrapProps}>
      <div className="legend-bar" aria-hidden>
        <span>{legendLo}</span>
        <span className="legend-grad" style={{ background: `linear-gradient(90deg, ${legendStops.join(",")})` }} />
        <span>{legendHi}</span>
        <span className="muted">{m.label}{mid !== null ? `（中央 ${m.fmt(m.mid!)}）` : ""}</span>
      </div>
      <svg width={W} height={H} className="chart" style={{ fontSize: fs }} role="img"
        aria-label={`${labels[0]}×${labels[1]}の${m.label}`}>
        {ys.map((yv, yi) => (
          <text key={yv} x={ml - 8} y={mt + ch * yi + ch / 2} dy="0.35em" textAnchor="end" className="tick">{yv}</text>
        ))}
        {xs.map((xv, xi) => (
          <text key={xv} x={ml + cw * xi + cw / 2} y={mt + ch * ys.length + fs * 1.3} textAnchor="middle" className="tick">{xv}</text>
        ))}
        {ys.map((yv, yi) =>
          xs.map((xv, xi) => {
            const r = cell.get(`${yv}\u0000${xv}`);
            const x = ml + cw * xi;
            const y = mt + ch * yi;
            if (!r) return <rect key={xv + yv} x={x + 1} y={y + 1} width={cw - 2} height={ch - 2} className="cell-empty" />;
            const v = metricValue(r, metric);
            const c = Number.isFinite(v) ? color(v) : ([228, 228, 228] as [number, number, number]);
            return (
              <g key={xv + yv}
                onMouseEnter={() => tip.setBody(tipBody(r.label, r))}>
                <rect x={x + 1} y={y + 1} width={cw - 2} height={ch - 2} rx={3} fill={rgb(c)} />
                <text x={x + cw / 2} y={y + ch / 2} dy="0.35em" textAnchor="middle"
                  fill={isDark(c) ? "#fff" : "#1c1c1e"} className="cell-text">
                  {Number.isFinite(v) ? m.fmt(v) : "–"}
                </text>
              </g>
            );
          }),
        )}
        <text x={ml + (cw * xs.length) / 2} y={H - fs * 0.6} textAnchor="middle" className="axis-label">{labels[1]}</text>
        <text transform={`translate(${fs * 1.1},${mt + (ch * ys.length) / 2}) rotate(-90)`} textAnchor="middle" className="axis-label">
          {labels[0]}
        </text>
      </svg>
      {tip.box}
    </div>
  );
}
