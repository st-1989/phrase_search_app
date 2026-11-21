import assert from "node:assert/strict";
import test from "node:test";
import { explorationKey, explorationRoot, explorationSpec, exploreFrom } from "../src/exploration.ts";

const spec = {
  dataset: "reports.csv", terms: ["強固"], mode: "report", excl: ["比較的強固"],
  year: [2020, 2025], sel: { "発行体格付(業務)": ["AA"], "業種(業務)": ["製造業"] },
  unit: "report", method: "fast", rank_by: "count", min_count: 1,
  top_n: 0, max_docs: 0, exclude_words: ["当社"],
};

test("起点変更は以前の語を外し、会社・格付・期間・除外などの条件を維持する", () => {
  const root = explorationRoot(spec);
  const next = exploreFrom(root, "財務基盤", "pivot");
  const active = explorationSpec(spec, next);
  assert.deepEqual(active, { ...spec, terms: ["財務基盤"], mode: "row" });
  assert.deepEqual(root.terms, ["強固"]);
  assert.deepEqual(spec.terms, ["強固"]);
});

test("絞り込みを繰り返すと3語以上が同じ文のAND条件になり、元の段階も維持する", () => {
  const root = explorationRoot(spec);
  const second = exploreFrom(root, "財務基盤", "narrow");
  const third = exploreFrom(second, "維持", "narrow");
  assert.deepEqual(third.terms, ["強固", "財務基盤", "維持"]);
  assert.equal(third.mode, "row");
  assert.deepEqual(exploreFrom(third, "財務基盤", "narrow").terms, third.terms);
  assert.deepEqual(explorationSpec(spec, second).terms, ["強固", "財務基盤"]);
  assert.deepEqual(explorationSpec(spec, root), spec);
});

test("複数語やOR検索からの絞り込みでも、全語を同じ文のAND条件にする", () => {
  const root = explorationRoot({ ...spec, terms: ["強固", "安定"], mode: "or" });
  assert.deepEqual(exploreFrom(root, "維持", "narrow"), {
    terms: ["強固", "安定", "維持"], mode: "row", action: "narrow",
  });
});

test("格付・期間・集計設定を変えても探索を維持し、新しい条件を反映する", () => {
  const changed = { ...spec, year: [2023, 2024], sel: { "発行体格付(業務)": ["A"] }, unit: "row", min_count: 5 };
  assert.equal(explorationKey(spec), explorationKey(changed));
  const step = exploreFrom(explorationRoot(spec), "財務基盤", "pivot");
  assert.deepEqual(explorationSpec(changed, step), { ...changed, terms: ["財務基盤"], mode: "row" });
  for (const patch of [{ dataset: "other.csv" }, { terms: ["維持"] }, { mode: "or" }, { excl: [] }]) {
    assert.notEqual(explorationKey(spec), explorationKey({ ...spec, ...patch }));
  }
});
