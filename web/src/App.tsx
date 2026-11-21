import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  DATASET_SEP, DEFAULT_YEAR_FROM, DEFAULT_YEAR_TO, FILTER_COLUMNS, FILTER_LABELS, MODES,
  datasetLabel, fetchDatasets, fetchTable, fileLabel, filterMask, findHits, sortHits, splitDataset,
  type Filters, type Mode, type Table,
} from "./data";
import { api, type History, type HistoryEntry, type Spec } from "./api";
import { usePersistent } from "./persist";
import { MultiFilter, YearFilter } from "./components/Filters";
import { Segmented, usePopover } from "./components/Popover";
import { ResultList, type ResultListHandle } from "./components/ResultList";
import { CountView } from "./views/CountView";
import { GroupTermsView, TermsView } from "./views/TermsView";
import { DEFAULT_ANALYSIS, type AnalysisSettings } from "./views/settings";

type Settings = { fontSize: number; context: number; font: string; customFont: string; theme: "auto" | "light" | "dark" };
const DEFAULT_SETTINGS: Settings = { fontSize: 18, context: 45, font: "BIZ UDPGothic", customFont: "", theme: "auto" };
// [CSS のフォント名, 表示名, Google Fonts から読み込むか]
const FONTS: [string, string, boolean][] = [
  ["BIZ UDPGothic", "BIZ UDPゴシック", true],
  ["BIZ UDGothic", "BIZ UDゴシック", true],
  ["BIZ UDPMincho", "BIZ UDP明朝", true],
  ["Noto Sans JP", "Noto Sans JP", true],
  ["Noto Serif JP", "Noto Serif JP", true],
  ["Zen Kaku Gothic New", "Zen Kaku Gothic New", true],
  ["IBM Plex Sans JP", "IBM Plex Sans JP", true],
  ["Hiragino Sans", "ヒラギノ角ゴ（Mac）", false],
  ["Yu Gothic", "游ゴシック", false],
  ["Meiryo", "メイリオ", false],
  ["custom", "その他（フォント名を入力）", true],
];

const VIEWS = [
  { id: "results", label: "検索結果" },
  { id: "count", label: "件数の集計" },
  { id: "terms", label: "共起語" },
  { id: "group", label: "区分別の共起語" },
] as const;
type View = (typeof VIEWS)[number]["id"];

export default function App() {
  const [settings, setSettings] = usePersistent<Settings>("ps.settings", DEFAULT_SETTINGS);
  const [analysis, setAnalysis] = usePersistent<AnalysisSettings>("ps.analysis", DEFAULT_ANALYSIS);
  const setA = useCallback((p: Partial<AnalysisSettings>) => setAnalysis((s) => ({ ...s, ...p })), [setAnalysis]);
  const [view, setView] = usePersistent<View>("ps.view", "results");
  const [datasets, setDatasets] = useState<{ id: string; size: number }[]>([]);
  const [datasetId, setDatasetId] = usePersistent<string>("ps.dataset", "");
  const [table, setTable] = useState<Table | null>(null);
  const [loadError, setLoadError] = useState("");
  const [janome, setJanome] = useState(false);

  const [user, setUser] = usePersistent<string>("ps.user", "ゲスト");
  const [history, setHistory] = useState<History>({ users: {} });

  const [q, setQ] = useState("");
  const [q2, setQ2] = useState("");
  const [excl, setExcl] = useState("");
  const [mode, setMode] = usePersistent<Mode>("ps.mode", "report");
  const [showSecond, setShowSecond] = useState(false);
  const [showExcl, setShowExcl] = useState(false);
  const [filters, setFilters] = useState<Filters>({ year: null, sel: {} });

  const searchRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<ResultListHandle>(null);

  // ---- 表示設定 ----
  useEffect(() => {
    const root = document.documentElement;
    const family = settings.font === "custom" ? settings.customFont.replace(/["';{}<>]/g, "").trim() : settings.font;
    root.style.setProperty("--font-size", `${settings.fontSize}px`);
    root.style.setProperty("--font", family ? `"${family}"` : "system-ui");
    if (settings.theme === "auto") delete root.dataset.theme;
    else root.dataset.theme = settings.theme;
    const web = FONTS.find((f) => f[0] === settings.font)?.[2];
    if (family && web) {
      const id = "gf-" + family;
      if (!document.getElementById(id)) {
        const link = document.createElement("link");
        link.id = id;
        link.rel = "stylesheet";
        link.href = `https://fonts.googleapis.com/css2?family=${family.replace(/ /g, "+")}:wght@400;700&display=swap`;
        document.head.appendChild(link);
      }
    }
  }, [settings]);

  // ---- データ・履歴 ----
  const refreshDatasets = useCallback(
    (select?: string) =>
      fetchDatasets()
        .then((ds) => {
          setDatasets(ds);
          if (select) setDatasetId(select);
          else setDatasetId((cur) => {
            // 前回のファイルのうち、今もあるものだけを残す
            const kept = splitDataset(cur).filter((x) => ds.some((d) => d.id === x));
            return kept.length ? kept.join(DATASET_SEP) : ds.find((d) => d.id.includes("sentences"))?.id ?? ds[0]?.id ?? "";
          });
        })
        .catch((e) => setLoadError(String(e.message ?? e))),
    [setDatasetId],
  );
  useEffect(() => {
    refreshDatasets();
    api.history().then(setHistory).catch(() => {});
    api.capabilities().then((c) => setJanome(c.janome)).catch(() => {});
  }, [refreshDatasets]);

  useEffect(() => {
    if (!datasetId) return;
    let cancelled = false;
    setTable(null);
    setLoadError("");
    fetchTable(datasetId)
      .then((t) => {
        if (cancelled) return;
        setTable(t);
        const b = t.yearBounds();
        let year: [number, number] | null = null;
        if (b && b[0] < b[1]) {
          const lo = Math.max(b[0], DEFAULT_YEAR_FROM);
          const hi = Math.min(b[1], DEFAULT_YEAR_TO);
          if (lo <= hi && (lo !== b[0] || hi !== b[1])) year = [lo, hi];
        }
        setFilters({ year, sel: {} });
      })
      .catch((e) => !cancelled && setLoadError(String(e.message ?? e)));
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  /** アップロードしたファイルを、いま読み込んでいるデータに追加する（add=false なら置き換える）。 */
  const onUpload = async (files: FileList | null, add: boolean) => {
    if (!files?.length) return;
    setLoadError("");
    try {
      const ids = add ? splitDataset(datasetId) : [];
      for (const f of Array.from(files)) {
        const { id } = await api.upload(f);
        if (!ids.includes(id)) ids.push(id);
      }
      await refreshDatasets(ids.join(DATASET_SEP));
    } catch (e) {
      setLoadError(String((e as Error).message ?? e));
    }
  };
  const uploadMode = useRef(true);
  const pickUpload = (add: boolean) => {
    uploadMode.current = add;
    fileRef.current?.click();
  };

  // ---- 検索（入力は即時、重い計算は後回し） ----
  const terms = useMemo(() => [q, q2].filter(Boolean), [q, q2]);
  const exclList = useMemo(() => excl.split(/\s+/).filter(Boolean), [excl]);
  const d = {
    terms: useDeferredValue(terms),
    exclList: useDeferredValue(exclList),
    mode: useDeferredValue(mode),
    filters: useDeferredValue(filters),
    table: useDeferredValue(table),
  };
  const mask = useMemo(() => (d.table ? filterMask(d.table, d.filters) : null), [d.table, d.filters]);
  const pool = useMemo(() => {
    if (!d.table) return 0;
    if (!mask) return d.table.n;
    let c = 0;
    for (const v of mask) c += v;
    return c;
  }, [d.table, mask]);
  const hits = useMemo(
    () => (d.table && d.terms.length ? findHits(d.table, d.terms, d.mode, d.exclList, mask) : null),
    [d.table, d.terms, d.mode, d.exclList, mask],
  );
  // 文の一覧の並び（年の新しい順 → 格付 → 業種）
  const sortedHits = useMemo(() => (d.table && hits ? sortHits(d.table, hits) : null), [d.table, hits]);
  const stale = d.terms !== terms || d.filters !== filters || d.mode !== mode || d.exclList !== exclList;

  // サーバーで分析するときの条件（入力が落ち着いた値を使う）
  const spec: Spec | null = useMemo(() => {
    if (!d.terms.length || !datasetId) return null;
    return {
      dataset: datasetId, terms: d.terms, mode: d.mode, excl: d.exclList,
      year: d.filters.year, sel: d.filters.sel,
      unit: d.table?.reportId ? analysis.unit : "row",
      method: analysis.method, rank_by: analysis.rankBy, min_count: analysis.minCount,
      top_n: analysis.topN, max_docs: analysis.maxDocs,
      exclude_words: analysis.excludeWords.split(/\s+/).filter(Boolean),
    };
  }, [datasetId, d.terms, d.mode, d.exclList, d.filters, d.table, analysis]);

  // ---- 履歴（入力が 1.2 秒止まったら記録。打ちかけの語はサーバー側で置き換える） ----
  useEffect(() => {
    if (!hits || !q || stale || !user) return;
    const timer = setTimeout(() => {
      api.record({ user, query: q, query2: q2, mode: q2 ? mode : "", exclude: excl.trim(), dataset: datasetId, hits: hits.length })
        .then(setHistory)
        .catch(() => {});
    }, 1200);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hits, stale]);

  const restore = (r: HistoryEntry) => {
    setQ(r.query);
    setQ2(r.query2);
    setExcl(r.exclude);
    if (r.mode) setMode(r.mode);
    setShowSecond(!!r.query2);
    setShowExcl(!!r.exclude);
    if (r.dataset && r.dataset !== datasetId && splitDataset(r.dataset).every((p) => datasets.some((x) => x.id === p))) {
      setDatasetId(r.dataset);
    }
    searchRef.current?.focus();
  };

  // ---- キーボード: / ⌘K で検索欄、1〜4 で表示の切り替え ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest?.("input, textarea, select");
      if ((e.key === "/" && !typing) || (e.key === "k" && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      } else if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey && /^[1-4]$/.test(e.key)) {
        setView(VIEWS[+e.key - 1].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setView]);

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      if (q) setQ("");
      else e.currentTarget.blur();
    } else if (e.key === "ArrowDown" && view === "results" && hits?.length) {
      e.preventDefault();
      listRef.current?.focusFirst();
    }
  };

  const yearBounds = table?.yearBounds() ?? null;
  const recent = history.users[user] ?? [];
  const showViews = !!(table && hits);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Phrase Search</span>
        <DataMenu datasets={datasets} value={datasetId} onChange={setDatasetId} table={table} onUpload={pickUpload} />
        <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" multiple hidden
          onChange={(e) => { onUpload(e.target.files, uploadMode.current); e.target.value = ""; }} />
        <span className="spacer" />
        <UserMenu
          user={user}
          users={Object.keys(history.users)}
          onPick={setUser}
          onAdd={(name) => api.addUser(name).then((h) => { setHistory(h); setUser(name); })}
        />
        <SettingsMenu settings={settings} onChange={setSettings} />
      </header>

      <main className={"main" + (view !== "results" && showViews ? " wide" : "")}>
        <section className="search">
          <div className="search-row">
            <SearchIcon />
            <input
              ref={searchRef}
              className="search-input"
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onSearchKey}
              placeholder="フレーズを入力（完全一致）"
              aria-label="検索フレーズ"
              spellCheck={false}
            />
            {q && (
              <button className="icon-btn" onClick={() => { setQ(""); searchRef.current?.focus(); }} aria-label="クリア">×</button>
            )}
            <kbd className="kbd-hint">/</kbd>
          </div>

          {showSecond && (
            <div className="sub-row">
              <span className="sub-label">＋ 2つ目の語</span>
              <input
                className="sub-input"
                autoFocus={!q2}
                value={q2}
                onChange={(e) => setQ2(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && (q2 ? setQ2("") : (setShowSecond(false), searchRef.current?.focus()))}
                placeholder="例: 改善"
                spellCheck={false}
              />
              <Segmented
                label="2語の条件"
                value={mode}
                onChange={setMode}
                options={MODES.filter((m) => m.id !== "report" || table?.reportId).map((m) => ({ id: m.id, label: m.label, hint: m.hint }))}
              />
              <button className="icon-btn" onClick={() => { setQ2(""); setShowSecond(false); }} aria-label="2つ目の語を外す">×</button>
            </div>
          )}

          {showExcl && (
            <div className="sub-row">
              <span className="sub-label">− 除外</span>
              <input
                className="sub-input"
                autoFocus={!excl}
                value={excl}
                onChange={(e) => setExcl(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && (excl ? setExcl("") : (setShowExcl(false), searchRef.current?.focus()))}
                placeholder="例: 比較的強固（スペース区切りで複数）"
                spellCheck={false}
              />
              <span className="sub-help">この語の中の一致は数えません</span>
              <button className="icon-btn" onClick={() => { setExcl(""); setShowExcl(false); }} aria-label="除外を外す">×</button>
            </div>
          )}

          <div className="chip-row">
            {!showSecond && <button className="chip ghost" onClick={() => setShowSecond(true)}>＋ 語を追加</button>}
            {!showExcl && <button className="chip ghost" onClick={() => setShowExcl(true)}>− 除外</button>}
            <span className="divider" />
            {table && yearBounds && yearBounds[0] < yearBounds[1] && (
              <YearFilter bounds={yearBounds} value={filters.year} onChange={(year) => setFilters((f) => ({ ...f, year }))} />
            )}
            {table &&
              FILTER_COLUMNS.filter((c) => table.has(c)).map((c) => (
                <MultiFilter
                  key={c}
                  label={FILTER_LABELS[c]}
                  options={table.options(c)}
                  value={filters.sel[c] ?? []}
                  onChange={(v) => setFilters((f) => ({ ...f, sel: { ...f.sel, [c]: v } }))}
                />
              ))}
            {(filters.year || Object.values(filters.sel).some((v) => v.length)) && (
              <button className="chip link" onClick={() => setFilters({ year: null, sel: {} })}>絞り込みを全て解除</button>
            )}
          </div>
        </section>

        {showViews ? (
          <nav className={"tabs" + (stale ? " stale" : "")} aria-label="表示">
            {VIEWS.map((v, i) => (
              <button key={v.id} className={view === v.id ? "on" : ""} onClick={() => setView(v.id)} title={`キー ${i + 1}`}>
                {v.label}
                {v.id === "results" && <span className="tab-count num">{hits!.length.toLocaleString()}</span>}
              </button>
            ))}
            <span className="spacer" />
            <span className="muted tab-meta">
              {hits!.length.toLocaleString()} 件 / {pool.toLocaleString()} 行
              {terms.length === 2 && <span className="pill">{MODES.find((m) => m.id === mode)?.label}</span>}
            </span>
          </nav>
        ) : (
          <div className="status" aria-live="polite">
            {loadError ? <span className="error">{loadError}</span>
              : !table ? <span className="muted">読み込み中…</span>
              : <span className="muted"><b className="num">{pool.toLocaleString()}</b> 行が対象（全 {table.n.toLocaleString()} 行
                {table.sources.length > 1 && <>・{table.sources.length} ファイル</>}
                {table.sources.some((x) => x.dropped) && <>・重複 {table.sources.reduce((a, x) => a + x.dropped, 0).toLocaleString()} 行を除外</>}）</span>}
          </div>
        )}

        {showViews && hits!.length === 0 && !stale && view !== "terms" && (
          <p className="empty">完全一致する句は見つかりませんでした。</p>
        )}

        {showViews && hits!.length > 0 && view === "results" && (
          <>
            <div className="hint-row muted">
              <kbd>↓</kbd> 結果へ <kbd>Enter</kbd> 全文 <kbd>Esc</kbd> 検索欄へ <kbd>1</kbd>–<kbd>4</kbd> 表示の切り替え
            </div>
            <ResultList ref={listRef} table={table!} rows={sortedHits!} terms={d.terms} excl={d.exclList}
              context={settings.context} dim={stale} onExit={() => searchRef.current?.focus()} />
          </>
        )}
        {showViews && hits!.length > 0 && view === "count" && (
          <CountView table={table!} mask={mask} hits={hits!} s={analysis} set={setA} fontSize={settings.fontSize} dim={stale} />
        )}
        {showViews && view === "terms" && spec && (
          <TermsView table={table!} spec={spec} s={analysis} set={setA} janome={janome}
            queries={d.terms} excl={d.exclList} context={settings.context} />
        )}
        {showViews && hits!.length > 0 && view === "group" && spec && (
          <GroupTermsView table={table!} spec={spec} s={analysis} set={setA} janome={janome}
            queries={d.terms} excl={d.exclList} context={settings.context} />
        )}

        {table && !terms.length && (
          <RecentList
            user={user}
            recent={recent}
            onPick={restore}
            onClear={() => api.clearHistory(user).then(setHistory)}
            onRemove={(r) => api.removeEntry(user, r).then(setHistory)}
          />
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------

/** データの選択。チェックしたファイルを、チェックした順につないで1つのデータとして検索する。 */
function DataMenu({ datasets, value, onChange, table, onUpload }: {
  datasets: { id: string; size: number }[]; value: string; onChange: (id: string) => void;
  table: Table | null; onUpload: (add: boolean) => void;
}) {
  const { open, setOpen, close, ref } = usePopover();
  const chosen = splitDataset(value);
  const toggle = (id: string) => {
    const next = chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id];
    if (next.length) onChange(next.join(DATASET_SEP));
  };
  const info = (id: string) => {
    const name = id.split("/").pop();
    return table && chosen.length > 1 ? table.sources.find((x) => x.name === name) : undefined;
  };
  return (
    <div className="pop-anchor" ref={ref}>
      <button className="quiet-select data-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="データ">
        {chosen.length ? fileLabel(chosen[0]) : "データを選択"}
        {chosen.length > 1 && <span className="pill">＋{chosen.length - 1}</span>}
        <span className="caret">▾</span>
      </button>
      {open && (
        <div className="popover data-pop">
          <div className="pop-hint muted">チェックしたファイルをまとめて検索します（前のファイルと重複する行は除きます）</div>
          <div className="pop-list">
            {datasets.map((x) => {
              const k = chosen.indexOf(x.id);
              const s = info(x.id);
              return (
                <label key={x.id} className="pop-item">
                  <input type="checkbox" checked={k >= 0} disabled={k >= 0 && chosen.length === 1} onChange={() => toggle(x.id)} />
                  <span className="data-name">{fileLabel(x.id)}</span>
                  {s && (
                    <span className="muted small num">
                      {s.rows.toLocaleString()} 行{s.dropped > 0 && `（重複 ${s.dropped.toLocaleString()} 行を除外）`}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          <div className="pop-foot">
            <button className="link" onClick={() => { onUpload(true); close(); }}>＋ ファイルを追加…</button>
            <button className="link" onClick={() => { onUpload(false); close(); }}>ファイルを開く…</button>
          </div>
        </div>
      )}
    </div>
  );
}

function UserMenu({ user, users, onPick, onAdd }: {
  user: string; users: string[]; onPick: (u: string) => void; onAdd: (name: string) => void;
}) {
  const { open, setOpen, close, ref } = usePopover();
  const [name, setName] = useState("");
  const add = () => {
    const n = name.trim();
    if (!n) return;
    onAdd(n);
    setName("");
    close();
  };
  return (
    <div className="pop-anchor" ref={ref}>
      <button className="user-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="ユーザー">
        <UserIcon /> {user} <span className="caret">▾</span>
      </button>
      {open && (
        <div className="popover right">
          <div className="pop-list">
            {(users.includes(user) ? users : [user, ...users]).map((u) => (
              <button key={u} className={"pop-item" + (u === user ? " on" : "")} onClick={() => { onPick(u); close(); }}>
                {u === user ? "✓ " : "　"}{u}
              </button>
            ))}
          </div>
          <div className="pop-add">
            <input placeholder="新しいユーザー名" value={name} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()} />
            <button className="link" disabled={!name.trim()} onClick={add}>追加</button>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsMenu({ settings, onChange }: { settings: Settings; onChange: (s: Settings) => void }) {
  const { open, setOpen, ref } = usePopover();
  const up = (p: Partial<Settings>) => onChange({ ...settings, ...p });
  return (
    <div className="pop-anchor" ref={ref}>
      <button className="icon-btn" onClick={() => setOpen((o) => !o)} aria-label="表示設定" aria-expanded={open}>
        <GearIcon />
      </button>
      {open && (
        <div className="popover right settings">
          <label>
            文字サイズ <b className="num">{settings.fontSize}px</b>
            <input type="range" min={12} max={32} value={settings.fontSize} onChange={(e) => up({ fontSize: +e.target.value })} />
          </label>
          <label>
            前後の表示文字数 <b className="num">{settings.context}</b>
            <input type="range" min={10} max={120} step={5} value={settings.context} onChange={(e) => up({ context: +e.target.value })} />
          </label>
          <label>
            フォント
            <select value={settings.font} onChange={(e) => up({ font: e.target.value })}>
              {FONTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
          {settings.font === "custom" && (
            <label>
              フォント名（Google Fonts の名前、または端末にあるフォント名）
              <input value={settings.customFont} onChange={(e) => up({ customFont: e.target.value })} placeholder="例: M PLUS 1p" />
            </label>
          )}
          <Segmented
            small
            label="テーマ"
            value={settings.theme}
            onChange={(theme) => up({ theme })}
            options={[{ id: "auto", label: "自動" }, { id: "light", label: "ライト" }, { id: "dark", label: "ダーク" }]}
          />
        </div>
      )}
    </div>
  );
}

function RecentList({ user, recent, onPick, onClear, onRemove }: {
  user: string; recent: HistoryEntry[]; onPick: (r: HistoryEntry) => void; onClear: () => void; onRemove: (r: HistoryEntry) => void;
}) {
  const [all, setAll] = useState(false);
  if (!recent.length) {
    return (
      <div className="empty">
        <p>フレーズを入力すると、その場で完全一致の結果が出ます。</p>
        <p className="muted">
          <kbd>/</kbd> 検索欄へ · <kbd>↓</kbd> 結果へ · <kbd>Enter</kbd> 全文 · <kbd>Esc</kbd> クリア · <kbd>1</kbd>–<kbd>4</kbd> 表示の切り替え
        </p>
        <p className="muted small">{user} さんの検索履歴はまだありません。</p>
      </div>
    );
  }
  const shown = all ? recent : recent.slice(0, 10);
  const modeLabel = (m: string) => MODES.find((x) => x.id === m)?.label;
  return (
    <section className="recent">
      <div className="recent-head">
        <span>{user} さんの最近の検索</span>
        <button className="link" onClick={onClear}>履歴を消去</button>
      </div>
      <ul>
        {shown.map((r) => (
          <li key={r.at + r.query + r.query2 + r.exclude}>
            <button className="recent-item" onClick={() => onPick(r)}>
              <span className="recent-q">
                {r.query}
                {r.query2 && <><span className="muted"> ＋ </span>{r.query2}</>}
                {r.exclude && <span className="muted"> − {r.exclude}</span>}
                {r.query2 && r.mode && <span className="pill">{modeLabel(r.mode)}</span>}
              </span>
              <span className="recent-meta">{r.hits.toLocaleString()} 件 · {r.at} · {datasetLabel(r.dataset)}</span>
            </button>
            <button className="icon-btn faint" onClick={() => onRemove(r)} aria-label="この履歴を削除">×</button>
          </li>
        ))}
      </ul>
      {recent.length > 10 && (
        <button className="link more" onClick={() => setAll((a) => !a)}>
          {all ? "少なく表示" : `さらに過去の履歴（${recent.length - 10} 件）`}
        </button>
      )}
    </section>
  );
}

const SearchIcon = () => (
  <svg className="search-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
  </svg>
);
const UserIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
  </svg>
);
const GearIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </svg>
);
