import type { CountSort, Metric, Unit } from "../stats";

/** 分析ビューの設定（ブラウザに保存）。 */
export type AnalysisSettings = {
  unit: Unit;
  groupCols: string[];
  metric: Metric;
  countSort: CountSort;
  minDocs: number;
  method: "fast" | "janome";
  rankBy: "count" | "score";
  minCount: number;
  topN: number; // 0 = 全て
  maxDocs: number; // 0 = 全件
  excludeWords: string;
};

export const DEFAULT_ANALYSIS: AnalysisSettings = {
  unit: "report",
  groupCols: ["発行体格付(業務)"],
  metric: "k",
  countSort: "axis",
  minDocs: 1,
  method: "fast",
  rankBy: "count",
  minCount: 3,
  topN: 0,
  maxDocs: 0,
  excludeWords: "",
};
