import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

const html = fs.readFileSync(new URL("../../search.html", import.meta.url), "utf8");
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1].split("(async function main()")[0];

function runtime() {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        innerHTML: "", textContent: "", value: "", hidden: false, attributes: {},
        classList: { add: (v) => classes.add(v), remove: (v) => classes.delete(v), contains: (v) => classes.has(v),
          toggle: (v, on) => on ? classes.add(v) : classes.delete(v) },
        querySelector: (s) => element(id + s), querySelectorAll: () => [],
        setAttribute(k, v) { this.attributes[k] = v; }, removeAttribute(k) { delete this.attributes[k]; },
      });
    }
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: element, addEventListener() {}, querySelector: element },
    window: { addEventListener() {} }, localStorage: { getItem: () => null },
    setTimeout: (fn, ms) => ms > 10 ? 0 : setTimeout(fn, ms), clearTimeout,
  });
  vm.runInContext(source, context);
  const api = vm.runInContext("({ S, buildTable, findHits, analyzeTerms, syncExploration, exploreTerm, goToTermStep, renderTerms, renderTermContext, recomputeMask, renderTermTrail, normalizeTermSettings })", context);
  const cols = {
    "本文": ["強固 財務基盤 維持 安定", "強固 財務基盤 維持 安定", "強固 需要 回復",
      "財務基盤 流動性 維持", "強固 財務基盤 維持 安定", "強固 財務基盤 見通し",
      "強固 維持", "財務基盤 流動性", "比較的強固 財務基盤 維持"],
    "発行体ID(業務)": ["X", "X", "X", "Y", "Z", "X", "M", "M", "N"],
    "公表日(業務)": ["2024-01-01", "2024-01-01", "2024-01-01", "2024-01-01", "2024-01-01", "2025-01-01", "2024-01-01", "2024-01-01", "2024-01-01"],
    "発行体格付(業務)": ["AA", "AA", "AA", "AA", "A", "AA", "AA", "AA", "AA"],
    "年(CDEX)": ["2024", "2024", "2024", "2024", "2024", "2025", "2024", "2024", "2024"],
  };
  api.S.table = api.buildTable(cols);
  api.S.terms = ["強固"];
  api.S.excl = ["比較的強固"];
  api.S.view = "terms";
  api.S.termSettings = { unit: "report", rankBy: "count", minCount: 1, topN: 50 };
  api.syncExploration();
  return { ...api, element, cols };
}

const values = (v) => JSON.parse(JSON.stringify(v));
const analyze = (r, terms, options = {}) => r.analyzeTerms(r.S.table, r.S.mask, terms, "row", r.S.excl, { ...r.S.termSettings, ...options });

test("HTML単体の3語検索は、別々の文にある語と除外フレーズを一致に含めない", () => {
  const r = runtime();
  assert.deepEqual(Array.from(r.findHits(["強固", "財務基盤", "維持"], "row", r.S.excl)), [0, 1, 4]);
  assert.deepEqual(Array.from(r.findHits([], "row", [])), []);
});

test("HTML単体の従来の同じレポート・OR条件を維持する", () => {
  const r = runtime();
  const report = Array.from(r.findHits(["強固", "財務基盤"], "report", r.S.excl));
  assert.ok(report.includes(6) && report.includes(7));
  assert.equal(r.findHits(["強固", "財務基盤"], "or", []).length, 9);
});

test("HTML単体はレポート内の繰り返しを1件とし、出現率とリフトを計算する", async () => {
  const r = runtime();
  const result = await analyze(r, ["強固", "財務基盤", "維持"]);
  const stable = result.terms.find((t) => t.term === "安定");
  assert.equal(result.nUnits, 6);
  assert.equal(result.nHit, 2);
  assert.equal(stable.docs, 2);
  assert.equal(stable.rate, 1);
  assert.equal(stable.bg_rate, 2 / 6);
  assert.equal(stable.lift, 1 / (2.5 / 7));
  assert.ok(!result.terms.some((t) => t.term === "需要"));
  assert.equal((await analyze(r, ["強固", "財務基盤", "維持"], { unit: "row" })).nHit, 3);
});

test("HTML単体は格付と年を絞った集合を比較対象にも使用する", async () => {
  const r = runtime();
  r.S.filters = { year: [2024, 2024], sel: { "発行体格付(業務)": new Set(["AA"]) } };
  r.recomputeMask();
  const result = await analyze(r, ["強固", "財務基盤", "維持"]);
  assert.equal(result.nUnits, 4);
  assert.equal(result.nHit, 1);
  assert.deepEqual(Array.from(result.hits), [0, 1]);
});

test("HTML単体は履歴をたどって起点変更・3語以上の絞り込み・戻る操作ができる", async () => {
  const r = runtime();
  await r.renderTerms();
  await r.exploreTerm("財務基盤", "narrow");
  await r.exploreTerm("維持", "narrow");
  assert.deepEqual(values(r.S.exploration.steps.at(-1).terms), ["強固", "財務基盤", "維持"]);
  assert.equal(r.S.exploration.result.nHit, 2);
  r.S.exploration.selected = "安定";
  r.renderTermContext();
  assert.ok(r.element("termContext").innerHTML.includes("3 文"));
  assert.ok(!r.element("termContext").innerHTML.includes("需要"));
  await r.exploreTerm("財務基盤", "pivot");
  assert.deepEqual(values(r.S.exploration.steps.at(-1).terms), ["財務基盤"]);
  assert.ok(r.S.exploration.result.terms.some((t) => t.term === "流動性"));
  await r.goToTermStep(0);
  assert.equal(r.S.exploration.steps.length, 1);
  assert.deepEqual(values(r.S.terms), ["強固"]);
});

test("HTML単体は絞り込み変更時に探索を維持し、検索語やデータの変更でリセットする", async () => {
  const r = runtime();
  await r.exploreTerm("財務基盤", "pivot");
  r.S.filters = { year: [2024, 2024], sel: {} };
  r.recomputeMask();
  r.syncExploration();
  assert.equal(r.S.exploration.steps.length, 2);
  for (const update of [() => { r.S.terms = ["維持"]; }, () => { r.S.datasetVersion++; }, () => { r.S.excl = []; }]) {
    update();
    r.syncExploration();
    assert.equal(r.S.exploration.steps.length, 1);
    await r.exploreTerm("財務基盤", "pivot");
  }
});

test("HTML単体はゼロ件・レポート識別列なしにも対応する", async () => {
  const r = runtime();
  const empty = await analyze(r, ["存在しない語"]);
  assert.equal(empty.nHit, 0);
  assert.equal(empty.terms.length, 0);
  r.S.table = r.buildTable({ "本文": ["強固 財務基盤", "財務基盤 維持"] });
  const fallback = await analyze(r, ["財務基盤"]);
  assert.equal(fallback.isReport, false);
  assert.equal(fallback.nUnits, 2);
});

test("HTML単体は最小出現数・表示語数・リフトによる並び替えを反映する", async () => {
  const r = runtime();
  assert.equal((await analyze(r, ["財務基盤"], { minCount: 99 })).terms.length, 0);
  const result = await analyze(r, ["財務基盤"], { topN: 1, rankBy: "lift" });
  assert.equal(result.terms.length, 1);
  assert.ok(result.totalTerms > 1);
  const sorted = await analyze(r, ["財務基盤"], { rankBy: "lift" });
  assert.ok(sorted.terms.every((t, i) => !i || sorted.terms[i - 1].lift >= t.lift));
});

test("HTML単体は古い非同期集計を破棄し、新しい探索の結果を上書きしない", async () => {
  const r = runtime();
  let cancelled = false;
  const pending = r.analyzeTerms(r.S.table, null, ["強固"], "row", [], r.S.termSettings, () => cancelled);
  cancelled = true;
  assert.equal(await pending, null);
  const first = r.renderTerms();
  const second = r.exploreTerm("財務基盤", "pivot");
  await Promise.all([first, second]);
  assert.deepEqual(values(r.S.exploration.steps.at(-1).terms), ["財務基盤"]);
  assert.ok(r.S.exploration.result.terms.some((t) => t.term === "流動性"));
});

test("HTML単体の探索履歴は入力をHTMLとして実行せず、保存設定も検証する", () => {
  const r = runtime();
  r.S.terms = ['<img src=x onerror="alert(1)">'];
  r.syncExploration();
  r.renderTermTrail();
  assert.ok(r.element("termTrail").innerHTML.includes("&lt;img"));
  assert.ok(!r.element("termTrail").innerHTML.includes("<img"));
  assert.deepEqual(values(r.normalizeTermSettings({ unit: "invalid", minCount: -5, topN: 9999, rankBy: "invalid" })),
    { unit: "report", rankBy: "count", minCount: 1, topN: 500 });
});
