"""共起探索の集計・使用例・絞り込みの整合性を検証する。"""
import unittest

import pandas as pd

from server.analysis import Analysis, find_hits


class CooccurrenceExplorationTests(unittest.TestCase):
    def setUp(self):
        texts = [
            "強固 財務基盤 維持 安定", "強固 財務基盤 維持 安定", "強固 需要 回復",
            "財務基盤 流動性 維持", "強固 財務基盤 維持 安定", "強固 財務基盤 見通し",
            "強固 維持", "財務基盤 流動性", "比較的強固 財務基盤 維持",
        ]
        self.df = pd.DataFrame({
            "本文": texts,
            "発行体ID(業務)": ["X", "X", "X", "Y", "Z", "X", "M", "M", "N"],
            "公表日(業務)": ["2024-01-01"] * 5 + ["2025-01-01"] + ["2024-01-01"] * 3,
            "発行体格付(業務)": ["AA"] * 4 + ["A"] + ["AA"] * 4,
            "年(CDEX)": [2024] * 5 + [2025] + [2024] * 3,
        })
        self.spec = {
            "terms": ["強固", "財務基盤", "維持"], "mode": "row", "unit": "report",
            "method": "fast", "min_count": 1, "excl": ["比較的強固"],
        }

    def test_three_words_require_same_sentence(self):
        hit = find_hits(self.df, self.spec["terms"], "row", tuple(self.spec["excl"]))
        self.assertEqual(list(self.df.index[hit]), [0, 1, 4])

    def test_report_counts_once_and_context_uses_only_matching_sentences(self):
        analysis = Analysis(self.df, self.spec)
        result = analysis.terms()
        stable = next(t for t in result["terms"] if t["term"] == "安定")
        self.assertEqual(result["n_hit_units"], 2)
        self.assertEqual(stable["docs"], 2)
        self.assertEqual(stable["rate"], 1.0)
        self.assertEqual(analysis.context("安定", None)["rows"], [0, 1, 4])
        self.assertNotIn("需要", [t["term"] for t in result["terms"]])

    def test_context_excludes_other_sentences_containing_only_some_constraints(self):
        frame = self.df.copy()
        frame.loc[2, "本文"] = "強固 安定 需要"
        analysis = Analysis(frame, self.spec)
        self.assertEqual(analysis.context("安定", None)["rows"], [0, 1, 4])

    def test_pivot_can_find_words_in_sentences_without_original_query(self):
        pivot = Analysis(self.df, {**self.spec, "terms": ["財務基盤"]})
        narrow = Analysis(self.df, self.spec)
        self.assertIn("流動性", [t["term"] for t in pivot.terms()["terms"]])
        self.assertNotIn("流動性", [t["term"] for t in narrow.terms()["terms"]])

    def test_filters_apply_to_foreground_background_and_context(self):
        analysis = Analysis(self.df, {**self.spec, "year": [2024, 2024], "sel": {"発行体格付(業務)": ["AA"]}})
        result = analysis.terms()
        self.assertEqual(result["n_units"], 4)
        self.assertEqual(result["n_hit_units"], 1)
        self.assertEqual(analysis.context("安定", None)["rows"], [0, 1])

    def test_no_matching_data_is_safe_for_report_mode(self):
        analysis = Analysis(self.df, {**self.spec, "mode": "report", "year": [1990, 1991]})
        self.assertEqual(analysis.terms()["terms"], [])
        self.assertEqual(analysis.terms()["n_units"], 0)

    def test_multiword_report_mode_allows_different_sentences(self):
        hit = find_hits(self.df, self.spec["terms"], "report", tuple(self.spec["excl"]))
        self.assertTrue(hit[6])
        self.assertTrue(hit[7])
        self.assertFalse(hit[5])
        self.assertFalse(hit[8])

    def test_sentence_unit_counts_sentences_instead_of_reports(self):
        result = Analysis(self.df, {**self.spec, "unit": "row"}).terms()
        self.assertEqual(result["n_hit_units"], 3)
        self.assertEqual(result["unit"], "row")

    def test_existing_two_word_modes_still_work(self):
        terms = ["強固", "財務基盤"]
        row = find_hits(self.df, terms, "row", ())
        report = find_hits(self.df, terms, "report", ())
        either = find_hits(self.df, terms, "or", ())
        self.assertFalse(row[6])
        self.assertTrue(report[6])
        self.assertTrue(report[7])
        self.assertTrue(either.all())


if __name__ == "__main__":
    unittest.main()
