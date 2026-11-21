import { useEffect, useMemo, useRef, useState } from "react";
import { GROUP_COLUMNS, GROUP_LABELS, type Table } from "../data";
import { api, type GroupTermsResult, type Spec, type TermRow, type TermsResult } from "../api";
import { AxisPicker } from "../components/Filters";
import { Control, Segmented, usePopover } from "../components/Popover";
import { ResultList } from "../components/ResultList";
import { UnitControl } from "./CountView";
import type { AnalysisSettings } from "./settings";
import { explorationKey, explorationRoot, explorationSpec, exploreFrom, type ExplorationAction, type ExplorationStep } from "../exploration";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

/** spec が変わったら少し待ってからサーバーに問い合わせる（前の問い合わせは取り消す）。 */
function useServer<T>(spec: Spec | null, fn: (s: Spec, signal: AbortSignal) => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [settledKey, setSettledKey] = useState("");
  const key = spec ? JSON.stringify(spec) : "";
  useEffect(() => {
    if (!spec) return;
    const ctl = new AbortController();
    setLoading(true);
    setError("");
    setSettledKey("");
    const timer = setTimeout(() => {
      fn(spec, ctl.signal)
        .then((d) => {
          if (!ctl.signal.aborted) { setData(d); setLoading(false); setSettledKey(key); }
        })
        .catch((e) => {
          if (!ctl.signal.aborted) { setData(null); setError(String(e.message ?? e)); setLoading(false); setSettledKey(key); }
        });
    }, 350);
    return () => { clearTimeout(timer); ctl.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return {
    data: settledKey === key ? data : null,
    loading: !!spec && (loading || settledKey !== key),
    error: settledKey === key ? error : "",
  };
}

function useExploration(spec: Spec) {
  const rootKey = explorationKey(spec);
  const [state, setState] = useState<{ rootKey: string; steps: ExplorationStep[] }>({ rootKey, steps: [explorationRoot(spec)] });
  useEffect(() => {
    setState((previous) => previous.rootKey === rootKey ? previous : { rootKey, steps: [explorationRoot(spec)] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rootKey]);
  const steps = state.rootKey === rootKey ? state.steps : [explorationRoot(spec)];
  const current = steps[steps.length - 1];
  const activeSpec = useMemo(() => explorationSpec(spec, current), [spec, current]);
  const explore = (term: string, action: ExplorationAction) => {
    setState({ rootKey, steps: [...steps, exploreFrom(current, term, action)] });
  };
  const goTo = (index: number) => setState({ rootKey, steps: steps.slice(0, index + 1) });
  return { steps, activeSpec, explore, goTo };
}

function ExplorationTrail({ steps, goTo }: { steps: ExplorationStep[]; goTo: (index: number) => void }) {
  const current = steps[steps.length - 1];
  const condition = current.terms.length === 1 ? "を含む文"
    : current.mode === "row" ? "のすべてを同じ文に含む"
    : current.mode === "or" ? "のいずれかを含む文" : "のすべてを同じレポートに含む";
  return (
    <section className="exploration" aria-label="共起語の探索">
      <nav className="exploration-trail" aria-label="探索履歴">
        {steps.map((step, i) => (
          <button key={i} className={"chip" + (i === steps.length - 1 ? " active" : " ghost")}
            aria-current={i === steps.length - 1 ? "step" : undefined} onClick={() => goTo(i)}>
            {i + 1}. {step.action === "root" ? "検索語" : step.action === "pivot" ? "起点" : "絞り込み"}：{step.terms.join("・")}
          </button>
        ))}
      </nav>
      <p className="caption" aria-live="polite">対象：<b>{current.terms.join("・")}</b>{condition}。共起語は検索条件に一致する文から抽出します。</p>
      {steps.length > 1 && (
        <div className="exploration-actions">
          <button className="link" onClick={() => goTo(steps.length - 2)}>1つ前に戻る</button>
          <button className="link" onClick={() => goTo(0)}>最初の検索語に戻る</button>
        </div>
      )}
    </section>
  );
}

function TermControls({ s, set, janome, table }: {
  s: AnalysisSettings; set: (p: Partial<AnalysisSettings>) => void; janome: boolean; table: Table;
}) {
  const more = usePopover();
  const changed = s.topN > 0 || s.maxDocs > 0 || s.excludeWords.trim() !== "";
  return (
    <>
      <UnitControl table={table} s={s} set={set} />
      <Control label="語の切り出し" hint="形態素解析（Janome）は精度が高い反面、初回は時間がかかります（結果は記憶されます）">
        <Segmented
          label="語の切り出し"
          value={s.method}
          onChange={(method) => set({ method })}
          options={[{ id: "fast", label: "高速" }, ...(janome ? [{ id: "janome" as const, label: "形態素解析" }] : [])]}
        />
      </Control>
      <Control label="並び順">
        <Segmented
          label="並び順"
          value={s.rankBy}
          onChange={(rankBy) => set({ rankBy })}
          options={[
            { id: "count", label: "出現数", hint: "その語を含む件数の多い順" },
            { id: "score", label: "特徴度", hint: "比較対象より特に多い語（対数尤度比 G²）" },
          ]}
        />
      </Control>
      <Control label="最小出現数" hint="その語を含む件数の下限">
        <input type="number" min={1} max={50} value={s.minCount} className="num-input"
          onChange={(e) => set({ minCount: Math.max(1, +e.target.value || 1) })} />
      </Control>
      <div className="pop-anchor" ref={more.ref}>
        <button className={"chip ghost" + (changed ? " active" : "")} onClick={() => more.setOpen((o) => !o)}>
          詳細{changed ? "（変更あり）" : ""} ▾
        </button>
        {more.open && (
          <div className="popover settings">
            <label>
              表示する語数（0 = 全て）
              <input type="number" min={0} value={s.topN} onChange={(e) => set({ topN: Math.max(0, +e.target.value || 0) })} />
            </label>
            <label>
              分析に使う最大件数（0 = 全件）
              <input type="number" min={0} step={100} value={s.maxDocs}
                onChange={(e) => set({ maxDocs: Math.max(0, +e.target.value || 0) })} />
              <span className="muted small">指定すると、一致・比較対象それぞれから無作為に抽出して速くします。</span>
            </label>
            <label>
              除外する語（スペース区切り）
              <input value={s.excludeWords} onChange={(e) => set({ excludeWords: e.target.value })} />
            </label>
          </div>
        )}
      </div>
    </>
  );
}

function ContextPanel({ table, spec, term, group, queries, excl, context, onClose, onExplore }: {
  table: Table; spec: Spec; term: string; group: string | null; queries: string[]; excl: string[];
  context: number; onClose: () => void;
  onExplore?: (term: string, action: ExplorationAction) => void;
}) {
  const [res, setRes] = useState<{ n_units: number; rows: number[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const ctl = new AbortController();
    setRes(null);
    setErr("");
    api.context(spec, term, group, ctl.signal).then(setRes).catch((e) => !ctl.signal.aborted && setErr(String(e.message ?? e)));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(spec), term, group]);
  const hl = useMemo(() => [...queries, term], [queries, term]);
  const rows = useMemo(() => (res ? Int32Array.from(res.rows) : null), [res]);
  const unitName = spec.unit === "report" ? "レポート" : "文";
  return (
    <section className="context-panel">
      <header>
        <span>
          <mark className="t2">{term}</mark> が{group ? <> <b>{group}</b> で</> : ""}検索語と同じ文に出てくる例
        </span>
        <button className="icon-btn" onClick={onClose} aria-label="閉じる">×</button>
      </header>
      {onExplore && (
        <div className="context-explore">
          <button className="chip" onClick={() => onExplore(term, "pivot")}
            title="以前の検索語の条件を外し、この語と同じ文に現れる語を調べます">この語を起点に探す</button>
          <button className="chip" onClick={() => onExplore(term, "narrow")}
            title="現在の検索語とこの語が、すべて同じ文にあるものに絞ります">現在の語との組み合わせで絞る</button>
        </div>
      )}
      {err && <p className="error">{err}</p>}
      {!res && !err && <p className="muted">読み込み中…</p>}
      {res && (
        <p className="caption">
          この語を含む{unitName} {res.n_units.toLocaleString()} 件 · {res.rows.length.toLocaleString()} 文
        </p>
      )}
      {rows && <ResultList table={table} rows={rows} terms={hl} excl={excl} context={context} className="inset" />}
    </section>
  );
}

export function TermsView({ table, spec, s, set, janome, excl, context }: {
  table: Table; spec: Spec; s: AnalysisSettings; set: (p: Partial<AnalysisSettings>) => void;
  janome: boolean; queries: string[]; excl: string[]; context: number;
}) {
  const { steps, activeSpec, explore, goTo } = useExploration(spec);
  const { data, loading, error } = useServer<TermsResult>(activeSpec, api.terms);
  const [term, setTerm] = useState<string | null>(null);
  const specKey = JSON.stringify(activeSpec);
  const prev = useRef(specKey);
  useEffect(() => {
    if (prev.current !== specKey) setTerm(null);
    prev.current = specKey;
  }, [specKey]);
  const unitName = data?.unit === "report" ? "レポート" : "文";
  const maxRate = Math.max(0.0001, ...(data?.terms ?? []).map((t) => t.rate));

  return (
    <div className="pane">
      <div className="controls">
        <TermControls s={s} set={set} janome={janome} table={table} />
        {loading && <span className="spinner">解析中…</span>}
      </div>
      <ExplorationTrail steps={steps} goTo={goTo} />
      {error && <p className="error">{error}</p>}
      {data && (
        <p className={"caption" + (loading ? " stale" : "")}>
          一致した{unitName} {data.n_fg.toLocaleString()} 件
          {data.n_fg < data.n_hit_units && `（全 ${data.n_hit_units.toLocaleString()} 件から抽出）`}を、
          全{unitName} {data.n_bg.toLocaleString()} 件と比較。語をクリックすると使用例と、次の語を探す操作を表示します。
        </p>
      )}
      <div className={"split" + (term ? " has-side" : "")}>
        <div className={"table-scroll" + (loading ? " stale" : "")}>
          {data && data.terms.length === 0 && <p className="empty">
            {data.n_hit_units === 0 ? "現在の探索条件に一致する文がありません。前の段階に戻るか、絞り込みを変更してください。"
              : "条件に合う語がありませんでした。最小出現数を下げてみてください。"}
          </p>}
          {data && data.terms.length > 0 && (
            <TermTable rows={data.terms} selected={term} onPick={setTerm} maxRate={maxRate} />
          )}
        </div>
        {term && data && (
          <ContextPanel key={specKey + term} table={table} spec={activeSpec} term={term} group={null} queries={activeSpec.terms} excl={excl}
            context={context} onClose={() => setTerm(null)} onExplore={explore} />
        )}
      </div>
    </div>
  );
}

function TermTable({ rows, selected, onPick, maxRate }: {
  rows: TermRow[]; selected: string | null; onPick: (t: string) => void; maxRate: number;
}) {
  return (
    <table className="data clickable">
      <thead>
        <tr>
          <th className="txt">語</th><th>出現件数</th><th>出現率</th><th>比較対象</th><th>リフト</th><th>特徴度 G²</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.term} className={r.term === selected ? "sel" : ""} onClick={() => onPick(r.term)} tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && onPick(r.term)}>
            <td className="txt"><b>{r.term}</b></td>
            <td>{r.docs.toLocaleString()}</td>
            <td>
              <span className="rate-cell">
                <span className="rate-bar" style={{ width: `${(r.rate / maxRate) * 100}%` }} />
                {pct(r.rate)}
              </span>
            </td>
            <td className="muted">{pct(r.bg_rate)}</td>
            <td>{r.lift.toFixed(1)}</td>
            <td>{r.g2.toFixed(1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function GroupTermsView({ table, spec, s, set, janome, queries, excl, context }: {
  table: Table; spec: Spec; s: AnalysisSettings; set: (p: Partial<AnalysisSettings>) => void;
  janome: boolean; queries: string[]; excl: string[]; context: number;
}) {
  const cols = s.groupCols.filter((c) => table.has(c));
  const gspec = useMemo(() => ({ ...spec, group_cols: cols }), [JSON.stringify(spec), cols.join("\u0000")]); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, loading, error } = useServer<GroupTermsResult>(cols.length ? gspec : null, api.groupTerms);
  const [pick, setPick] = useState<{ group: string; term: string } | null>(null);
  const specKey = JSON.stringify(gspec);
  const prev = useRef(specKey);
  useEffect(() => {
    if (prev.current !== specKey) setPick(null);
    prev.current = specKey;
  }, [specKey]);
  const unitName = data?.unit === "report" ? "レポート" : "文";

  return (
    <div className="pane">
      <div className="controls">
        <Control label="軸（選んだ順）">
          <AxisPicker options={GROUP_COLUMNS.filter((c) => table.has(c))} labels={GROUP_LABELS} value={cols}
            onChange={(v) => set({ groupCols: v })} />
        </Control>
        <TermControls s={s} set={set} janome={janome} table={table} />
        {loading && <span className="spinner">解析中…</span>}
      </div>
      {!cols.length && <p className="empty">集計の軸を1つ以上選んでください。</p>}
      {error && <p className="error">{error}</p>}
      {data && cols.length > 0 && (
        <p className={"caption" + (loading ? " stale" : "")}>
          {cols.map((c) => GROUP_LABELS[c] ?? c).join(" × ")}ごとに、一致した{unitName}（{data.n_fg.toLocaleString()} 件）の中で
          {s.rankBy === "score" ? "その区分に特に多い語" : "出現数が多い語"}。数字は出現件数。語をクリックで使用例。
        </p>
      )}
      {data && cols.length > 0 && (
        <div className={"split vertical" + (pick ? " has-side" : "")}>
          <div className={"group-grid" + (loading ? " stale" : "")}>
            {data.groups.length === 0 && <p className="empty">件数が足りず表示できませんでした。</p>}
            {data.groups.map((g) => (
              <section key={g.label} className="group-col">
                <header><b>{g.label}</b><span className="muted"> {g.n.toLocaleString()}件</span></header>
                <ol>
                  {g.terms.map((t) => (
                    <li key={t.term}>
                      <button
                        className={pick?.group === g.label && pick.term === t.term ? "sel" : ""}
                        onClick={() => setPick({ group: g.label, term: t.term })}
                      >
                        <span>{t.term}</span>
                        <span className="muted num">{t.docs}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </section>
            ))}
          </div>
          {data.skipped.length > 0 && (
            <details className="note">
              <summary>件数が少ないため {data.skipped.length} 区分を省略（表示）</summary>
              {data.skipped.map((x) => `${x.label}（${x.n}件）`).join("、")}
            </details>
          )}
          {pick && (
            <ContextPanel key={specKey + pick.group + pick.term} table={table} spec={gspec} term={pick.term} group={pick.group} queries={queries} excl={excl}
              context={context} onClose={() => setPick(null)} />
          )}
        </div>
      )}
    </div>
  );
}
