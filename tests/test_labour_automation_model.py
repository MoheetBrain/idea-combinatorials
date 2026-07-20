"""Focused synthetic-fixture tests for the labour/automation evidence model."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


industry = load_script("11_build_industry_occupation_model.py")
automation = load_script("12_build_task_automation_model.py")
common = load_script("labour_automation_common.py")


class LabourAutomationModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = yaml.safe_load((ROOT / "config/automation_rubric.yaml").read_text(encoding="utf-8"))

    def test_exact_naics_occupation_mapping(self) -> None:
        archetype = {"canonical_name": "Cafe", "naics_codes": '["722000"]', "parent_archetype_name": "Restaurants and prepared-food services", "requires_manual_review": "false"}
        code, method, confidence, manual, _ = industry.choose_industry(archetype, {"722000"}, {})
        self.assertEqual((code, method), ("722000", "exact_detailed_naics"))
        self.assertGreater(confidence, 0.8)
        self.assertFalse(manual)

    def test_parent_industry_fallback(self) -> None:
        archetype = {"canonical_name": "Cafe", "naics_codes": '["722513"]', "parent_archetype_name": "Restaurants and prepared-food services", "requires_manual_review": "false"}
        code, method, confidence, _, _ = industry.choose_industry(archetype, {"722000"}, {})
        self.assertEqual((code, method), ("722000", "parent_naics"))
        self.assertLess(confidence, 0.8)

    def test_evidence_grade_degrades_for_family_fallback(self) -> None:
        archetype = {"canonical_name": "Cafe", "naics_codes": "[]", "parent_archetype_name": "Food", "requires_manual_review": "false"}
        code, method, confidence, manual, _ = industry.choose_industry(archetype, {"722000"}, {"Food": "722000"})
        self.assertEqual((code, method), ("722000", "business_family_fallback"))
        self.assertLess(confidence, 0.5)
        self.assertTrue(manual)

    def test_ambiguous_soc_crosswalk_preserves_one_to_many(self) -> None:
        rows = [
            {"left_taxonomy": "ONET_SOC_2019", "left_code": "11-1000.01", "right_taxonomy": "US_SOC_2018", "right_code": "11-1000"},
            {"left_taxonomy": "ONET_SOC_2019", "left_code": "11-1000.02", "right_taxonomy": "US_SOC_2018", "right_code": "11-1000"},
            {"left_taxonomy": "ESCO_ISCO", "left_code": "1234.a", "right_taxonomy": "ONET_SOC_2019", "right_code": "11-1000.01"},
            {"left_taxonomy": "ESCO_ISCO", "left_code": "5678.b", "right_taxonomy": "ONET_SOC_2019", "right_code": "11-1000.02"},
            {"left_taxonomy": "UK_SOC_2020", "left_code": "1111", "right_taxonomy": "ISCO_08", "right_code": "1234"},
            {"left_taxonomy": "UK_SOC_2020", "left_code": "2222", "right_taxonomy": "ISCO_08", "right_code": "5678"},
        ]
        self.assertEqual(industry.build_uk_bridge(rows)["11-1000"], ["1111", "2222"])

    def test_task_weight_is_labelled_proxy_not_time(self) -> None:
        scored_task = {
            "occupation_code": "11-1000.01", "occupation_title": "Synthetic occupation",
            "task_id": "t1", "task_statement": "Maintain records", "task_weight_proxy": 0.8,
            "scores": {name: (0.5 if name == "llm_assistance" else 0.0) for name in automation.MODALITIES},
            "end_to_end": 0.0, "workflow": 0.45, "reliable": 0.0, "headcount": 0.0,
            "residual_reasons": [], "published_llm_exposure": 0.5,
        }
        crosswalks = [{"left_taxonomy": "ONET_SOC_2019", "left_code": "11-1000.01", "right_taxonomy": "US_SOC_2018", "right_code": "11-1000"}]
        mix = [{"occupation_code": "11-1000", "occupation_title": "Synthetic occupation"}]
        priors, _, _, _ = automation.build_occupation_priors({("11-1000.01", "t1"): scored_task}, crosswalks, mix)
        method = priors["11-1000"]["task_weight_proxy_method"].casefold()
        self.assertTrue("not" in method or "never" in method)
        self.assertIn("hours", method)

    def test_physical_task_rejected_for_llm_only_automation(self) -> None:
        dimensions, _ = automation.classify_dimensions("Clean equipment and carry materials", self.rules)
        scores = automation.modality_scores("Clean equipment and carry materials", dimensions, 1.0, self.rules)
        self.assertEqual(dimensions["physical_manipulation_requirement"], "high")
        self.assertLessEqual(scores["llm_assistance"], 0.25)
        self.assertEqual(scores["software_agent_execution"], 0.0)

    def test_high_assistance_can_have_zero_headcount_reducibility(self) -> None:
        dimensions = {name: "unknown" for name in self.rules["dimensions"]}
        dimensions.update({
            "digital_input_availability": "high", "digital_output_acceptability": "high",
            "language_reasoning_requirement": "high", "structured_rule_content": "high",
            "human_presence_requirement": "high", "verification_observability": "high",
        })
        scores = automation.modality_scores("Answer inquiries and update records", dimensions, 0.9, self.rules)
        end_to_end, _, _, headcount = automation.derived_transitions(scores, dimensions)
        self.assertGreater(scores["llm_assistance"], 0.8)
        self.assertGreater(end_to_end, 0.5)
        self.assertEqual(headcount, 0.0)

    def test_minimum_staffing_constraint_blocks_single_worker_reduction(self) -> None:
        floor, on_site, remote, headcount, reason = automation.archetype_constraints("Childcare centre", self.rules, 1.0, 0.7)
        self.assertIsNone(floor)
        self.assertEqual((on_site, remote, headcount), ("required", "no", 0.0))
        self.assertIn("Single-worker", reason)

    def test_residual_physical_task_is_preserved(self) -> None:
        reasons = automation.residual_reasons("Clean the site and replenish supplies", self.rules)
        self.assertIn("physical_handling", reasons)

    def test_unknown_scores_remain_null(self) -> None:
        dimensions, matched = automation.classify_dimensions("Contemplate novel circumstances", self.rules)
        scores = automation.modality_scores("Contemplate novel circumstances", dimensions, None, self.rules)
        self.assertEqual(matched, [])
        self.assertTrue(all(value is None for value in scores.values()))
        self.assertTrue(all(value == "unknown" for value in dimensions.values()))

    def test_stable_deterministic_ids(self) -> None:
        first = common.stable_id("task", "11-1000", "t1")
        second = common.stable_id("task", "11-1000", "t1")
        changed = common.stable_id("task", "11-1000", "t2")
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_reproducible_parquet_output(self) -> None:
        rows = [{
            "occupation_record_id": "occ_1", "source_taxonomy": "TEST",
            "source_occupation_code": "01", "source_occupation_title": "Test worker",
            "source_description": None, "parent_code": None, "hierarchy_level": "unit",
            "release_version": "1", "source_id": "fixture", "provenance_json": "{}",
        }]
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.parquet"
            second = Path(directory) / "second.parquet"
            common.write_parquet(first, rows, common.ONTOLOGY_SCHEMA, ["source_taxonomy", "source_occupation_code"])
            common.write_parquet(second, rows, common.ONTOLOGY_SCHEMA, ["source_taxonomy", "source_occupation_code"])
            self.assertEqual(common.sha256(first), common.sha256(second))
            self.assertEqual(pq.read_table(first).to_pylist(), rows)

    def test_no_financial_recommendation_fields_in_primary_schema(self) -> None:
        names = set(common.OPPORTUNITY_SCHEMA.names)
        prohibited = {"owner_cash_flow", "investment_recommendation", "monte_carlo_probability", "profit"}
        self.assertFalse(names & prohibited)


if __name__ == "__main__":
    unittest.main()
