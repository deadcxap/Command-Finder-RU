import json
import math
import unittest

from benchmarks.metrics import evaluate


def case(case_id="one", expected=None, category="files", forbidden=None, tags=None):
    return {
        "id": case_id,
        "category": category,
        "expected_commands": ["wanted"] if expected is None else expected,
        "forbidden_commands": [] if forbidden is None else forbidden,
        "tags": [] if tags is None else tags,
    }


class RankingMetricsTests(unittest.TestCase):
    def test_duplicate_predictions_do_not_take_extra_rank_positions(self):
        result = evaluate([case()], {"one": ["wrong", "wrong", "wanted", "wanted"]})
        row = result["per_case"]["one"]
        self.assertEqual(row["ranking"], ["wrong", "wanted"])
        self.assertEqual(row["first_relevant_rank"], 2)
        self.assertEqual(result["overall"]["hit@1"], 0.0)
        self.assertEqual(result["overall"]["hit@3"], 1.0)
        self.assertEqual(result["overall"]["mrr@7"], 0.5)

    def test_any_acceptable_command_counts_as_hit_and_recall_covers_all(self):
        result = evaluate([case(expected=["first", "second"])], {"one": ["second"]})
        overall = result["overall"]
        self.assertEqual(overall["hit@1"], 1.0)
        self.assertEqual(overall["recall@1"], 0.5)
        self.assertEqual(overall["ndcg@1"], 1.0)
        self.assertAlmostEqual(overall["ndcg@3"], 1 / (1 + 1 / math.log2(3)))

    def test_ndcg_uses_relevant_ranks_and_ideal_binary_order(self):
        result = evaluate(
            [case(expected=["a", "b"])],
            {"one": ["a", "noise", "b"]},
            ks=(1, 3),
        )
        expected = (1.0 + 1 / math.log2(4)) / (1.0 + 1 / math.log2(3))
        self.assertAlmostEqual(result["overall"]["ndcg@3"], expected)
        self.assertEqual(result["overall"]["recall@3"], 1.0)
        self.assertEqual(result["overall"]["mrr@7"], 1.0)

    def test_missing_and_explicit_empty_predictions_are_failures(self):
        result = evaluate([case("hit"), case("missing"), case("empty")], {"hit": ["wanted"], "empty": []})
        self.assertEqual(result["evaluated_case_count"], 3)
        self.assertAlmostEqual(result["overall"]["hit@3"], 1 / 3)
        self.assertEqual(result["per_case"]["missing"]["ranking"], [])
        self.assertEqual(result["per_case"]["empty"]["mrr@7"], 0.0)

    def test_mrr_seven_excludes_rank_eight_even_with_larger_cutoff(self):
        ranking = [f"noise-{i}" for i in range(7)] + ["wanted"]
        result = evaluate([case()], {"one": ranking}, ks=(1, 7, 8))
        self.assertEqual(result["per_case"]["one"]["first_relevant_rank"], 8)
        self.assertEqual(result["overall"]["mrr@7"], 0.0)
        self.assertEqual(result["overall"]["hit@7"], 0.0)
        self.assertEqual(result["overall"]["hit@8"], 1.0)

    def test_out_of_scope_has_no_quality_or_safety_credit(self):
        cases = [
            case("known"),
            case("ood", expected=[], category="out_of_scope", forbidden=["dangerous"]),
            case("tagged", expected=[], tags=["out_of_scope"]),
        ]
        result = evaluate(cases, {"known": ["wrong"], "ood": ["dangerous"]})
        self.assertEqual(result["case_count"], 3)
        self.assertEqual(result["evaluated_case_count"], 1)
        self.assertEqual(result["overall"]["case_count"], 1)
        self.assertEqual(result["overall"]["hit@1"], 0.0)
        self.assertIsNone(result["overall"]["forbidden_hit@1"])
        self.assertIsNone(result["per_case"]["ood"]["hit@1"])
        self.assertEqual(result["out_of_scope"]["case_count"], 2)
        self.assertEqual(result["out_of_scope"]["predicted_case_count"], 1)
        self.assertEqual(result["out_of_scope"]["empty_ranking_count"], 1)
        self.assertNotIn("out_of_scope", result["by_category"])
        self.assertNotIn("out_of_scope", result["by_tag"])

    def test_forbidden_rates_use_only_annotated_cases(self):
        cases = [
            case("unsafe", forbidden=["delete"]),
            case("safe", forbidden=["delete"]),
            case("unannotated"),
        ]
        rankings = {"unsafe": ["delete", "wanted"], "safe": ["wanted", "other", "delete"], "unannotated": ["delete"]}
        result = evaluate(cases, rankings)
        self.assertEqual(result["overall"]["forbidden_case_count"], 2)
        self.assertEqual(result["overall"]["forbidden_hit@1"], 0.5)
        self.assertEqual(result["overall"]["forbidden_hit@3"], 1.0)
        self.assertIsNone(result["per_case"]["unannotated"]["forbidden_hit@1"])

    def test_category_macro_does_not_weight_large_categories_more(self):
        cases = [case("a"), case("b"), case("c"), case("d", category="git")]
        result = evaluate(cases, {"a": ["wanted"], "b": ["wanted"], "c": ["wanted"]})
        self.assertEqual(result["overall"]["hit@1"], 0.75)
        self.assertEqual(result["by_category"]["files"]["case_count"], 3)
        self.assertEqual(result["by_category"]["git"]["hit@1"], 0.0)
        self.assertEqual(result["macro_by_category"]["category_count"], 2)
        self.assertEqual(result["macro_by_category"]["hit@1"], 0.5)

    def test_macro_forbidden_rate_omits_unannotated_categories(self):
        cases = [case("a", forbidden=["delete"]), case("b", category="git")]
        result = evaluate(cases, {"a": ["delete"], "b": ["wanted"]})
        self.assertEqual(result["macro_by_category"]["forbidden_category_count"], 1)
        self.assertEqual(result["macro_by_category"]["forbidden_hit@1"], 1.0)

    def test_tag_slices_may_overlap(self):
        cases = [case("a", tags=["negation", "colloquial"]), case("b", tags=["negation"])]
        result = evaluate(cases, {"a": ["wanted"]})
        self.assertEqual(result["by_tag"]["negation"]["case_count"], 2)
        self.assertEqual(result["by_tag"]["negation"]["hit@1"], 0.5)
        self.assertEqual(result["by_tag"]["colloquial"]["hit@1"], 1.0)

    def test_empty_dataset_has_null_metrics_not_nan(self):
        result = evaluate([], {})
        self.assertEqual(result["case_count"], 0)
        self.assertEqual(result["overall"]["case_count"], 0)
        self.assertIsNone(result["overall"]["hit@1"])
        self.assertIsNone(result["macro_by_category"]["mrr@7"])
        self.assertEqual(result["by_category"], {})
        json.dumps(result, allow_nan=False)

    def test_only_out_of_scope_is_also_a_valid_empty_quality_set(self):
        result = evaluate([case(expected=[], category="out_of_scope")], {"one": ["anything"]})
        self.assertEqual(result["evaluated_case_count"], 0)
        self.assertIsNone(result["overall"]["mrr@7"])
        self.assertEqual(result["out_of_scope"]["predicted_case_count"], 1)

    def test_invalid_cutoffs_fail(self):
        for ks in ((), (0,), (-1,), (1, 1), (True,), (1.0,), "1"):
            with self.subTest(ks=ks), self.assertRaises(ValueError):
                evaluate([], {}, ks=ks)

    def test_unknown_prediction_ids_fail(self):
        with self.assertRaisesRegex(ValueError, "unknown case IDs"):
            evaluate([case()], {"typo": []})

    def test_duplicate_case_ids_fail(self):
        with self.assertRaisesRegex(ValueError, "duplicate case id"):
            evaluate([case(), case()], {})

    def test_inconsistent_labels_fail(self):
        invalid = [
            case(expected=[]),
            case(expected=["wanted"], category="out_of_scope"),
            case(expected=["same"], forbidden=["same"]),
            case(expected=["same", "same"]),
            case(tags=["same", "same"]),
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                evaluate([item], {})

    def test_malformed_cases_and_rankings_fail(self):
        bad_cases = [None, {}, [None], [{"id": "one", "category": "files"}], [case(case_id="")]]
        for items in bad_cases:
            with self.subTest(cases=items), self.assertRaises(ValueError):
                evaluate(items, {})
        for rankings in ([], {"one": "wanted"}, {"one": [""]}, {"one": [123]}, {1: []}):
            with self.subTest(rankings=rankings), self.assertRaises(ValueError):
                evaluate([case()], rankings)


if __name__ == "__main__":
    unittest.main()
