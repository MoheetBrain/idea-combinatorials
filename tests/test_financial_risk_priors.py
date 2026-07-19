"""Synthetic unit tests for the financial, labour, and risk prior pipeline."""

from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parser_module = load_module("parse_financial_risk_sources", "scripts/06_parse_financial_risk_sources.py")
builder_module = load_module("build_archetype_priors", "scripts/07_build_archetype_priors.py")


class ParserTests(unittest.TestCase):
    def test_sic_leading_zero_preserved(self) -> None:
        self.assertEqual(parser_module.code("01.10"), "0110")
        self.assertEqual(parser_module.code("1"), "01")

    def test_suppression_tokens_are_null(self) -> None:
        for token in ("[c]", "[low]", "x", "*", "..", ":"):
            self.assertIsNone(parser_module.number(token))

    def test_million_conversion_is_explicit(self) -> None:
        self.assertEqual(parser_module.number(2.5, 1_000_000), 2_500_000)

    def test_code_label_parsing_exact(self) -> None:
        self.assertEqual(parser_module.split_code_label("  051 : Mining of hard coal"), ("051", "Mining of hard coal"))

    def test_deterministic_observation_id(self) -> None:
        left = parser_module.stable_id("obs", "01", 2024, "UK")
        right = parser_module.stable_id("obs", "01", 2024, "UK")
        self.assertEqual(left, right)


class DerivationTests(unittest.TestCase):
    def test_division_by_zero_returns_null(self) -> None:
        self.assertIsNone(builder_module.div(100.0, 0.0))
        self.assertIsNone(builder_module.div(100.0, None))

    def test_financial_ratio_formula(self) -> None:
        self.assertAlmostEqual(builder_module.div(25.0, 100.0), 0.25)

    def test_two_cv_bounds(self) -> None:
        low, high = builder_module.bounded_ratio(20.0, 100.0, 0.05, 0.02)
        self.assertLess(low, 0.20)
        self.assertGreater(high, 0.20)

    def test_missing_cv_leaves_interval_null(self) -> None:
        self.assertEqual(builder_module.bounded_ratio(20.0, 100.0, None, 0.02), (None, None))

    def test_binned_quantile_interpolation(self) -> None:
        bands = [
            {"turnover_size_band": "0-49", "band_count": 50.0},
            {"turnover_size_band": "50-99", "band_count": 50.0},
        ]
        median = builder_module.binned_quantile(bands, 0.5, 1000)
        self.assertEqual(median, 49_000.0)

    def test_open_ended_quantile_is_not_invented(self) -> None:
        bands = [{"turnover_size_band": "50000+", "band_count": 10.0}]
        self.assertIsNone(builder_module.binned_quantile(bands, 0.9, 1000))

    def test_operating_surplus_proxy_not_profit(self) -> None:
        agva, employment_cost = 40.0, 25.0
        proxy = agva - employment_cost
        self.assertEqual(proxy, 15.0)
        self.assertNotIn("profit", builder_module.FINANCIAL_PRIOR_SCHEMA.names)


class MappingTests(unittest.TestCase):
    def test_exact_mapping_wins(self) -> None:
        mapping = {"5610": {"value": 1}, "56": {"value": 2}}
        code, row, distance = builder_module.closest("5610", mapping)
        self.assertEqual((code, row["value"], distance), ("5610", 1, 0))

    def test_parent_fallback_is_visible(self) -> None:
        mapping = {"56": {"value": 2}}
        code, _, distance = builder_module.closest("56102", mapping)
        self.assertEqual(code, "56")
        self.assertGreater(distance, 0)

    def test_domain_fallback_is_grade_d(self) -> None:
        self.assertEqual(builder_module.degrade("B", 0, domain_fallback=True), "D")

    def test_parent_fallback_reduces_grade(self) -> None:
        self.assertEqual(builder_module.degrade("B", 1), "C")

    def test_prohibited_grade_f_never_generated(self) -> None:
        grades = {builder_module.degrade("B", 0), builder_module.degrade("B", 1), builder_module.degrade("B", 0, True)}
        self.assertEqual(grades, {"B", "C", "D"})

    def test_no_final_decision_fields(self) -> None:
        names = {name.casefold() for name in builder_module.ARCHETYPE_PRIOR_SCHEMA.names}
        self.assertFalse(names & {"profit", "ebitda", "monthly_revenue", "probability_earning_15000", "automation_percentage"})

    def test_stable_json_is_key_order_independent(self) -> None:
        self.assertEqual(builder_module.stable_json({"b": 2, "a": 1}), builder_module.stable_json({"a": 1, "b": 2}))

    def test_no_nonfinite_synthetic_fixture(self) -> None:
        values = [builder_module.div(1.0, 2.0), builder_module.div(1.0, 0.0)]
        self.assertTrue(all(value is None or math.isfinite(value) for value in values))


if __name__ == "__main__":
    unittest.main()
