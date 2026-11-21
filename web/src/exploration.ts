import type { Spec } from "./api";

export type ExplorationAction = "pivot" | "narrow";
export type ExplorationStep = Pick<Spec, "terms" | "mode"> & { action: "root" | ExplorationAction };

/** 表示・集計条件を変えても探索は維持し、検索語・データ・除外条件が変われば最初から始める。 */
export function explorationKey(spec: Spec): string {
  return JSON.stringify([spec.dataset, spec.terms, spec.mode, spec.excl]);
}

export function explorationRoot(spec: Spec): ExplorationStep {
  return { terms: [...spec.terms], mode: spec.mode, action: "root" };
}

export function exploreFrom(step: ExplorationStep, term: string, action: ExplorationAction): ExplorationStep {
  return {
    terms: action === "pivot" ? [term] : [...new Set([...step.terms, term])],
    mode: "row",
    action,
  };
}

/** 年・格付・業種・集計単位などは現在の画面の設定をそのまま使用する。 */
export function explorationSpec(spec: Spec, step: ExplorationStep): Spec {
  return { ...spec, terms: step.terms, mode: step.mode };
}
