"""Focused tests for deterministic archetype construction and validation logic."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / "scripts" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


build = load_script("build_archetypes_v0", "03_build_archetypes.py")
validate = load_script("validate_archetypes_v0", "04_validate_archetypes.py")


def candidate(
    candidate_id: str,
    name: str,
    *,
    family: str = "laundry_services",
    flags: tuple[str, ...] = (),
    keywords: tuple[str, ...] | None = None,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "canonical_name": name,
        "canonical_key": build.canonical_key(name),
        "family_id": family,
        "distinction_flags": list(flags),
        "important_keywords": list(keywords or build.important_keywords(name)),
    }


class BuildArchetypeLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = build.load_rules()

    def test_exact_equivalent_merge(self) -> None:
        left = candidate(
            "left",
            "Self-storage facility",
            family="storage_services",
            flags=("fixed_site", "rental", "sale", "storage"),
        )
        right = candidate(
            "right",
            "Self-storage facility",
            family="storage_services",
            flags=("fixed_site", "rental", "sale", "storage"),
        )
        decision, reason = build.adjudicate_pair(left, right, 1.0, self.rules)
        self.assertEqual(decision, "SAME_ARCHETYPE")
        self.assertIn("Exact cleaned", reason)

    def test_related_but_distinct_non_merge(self) -> None:
        left = candidate("left", "Self-service laundry", flags=("self_service",))
        right = candidate("right", "Full-service laundry", flags=("full_service",))
        decision, _ = build.adjudicate_pair(left, right, 0.9, self.rules)
        self.assertEqual(decision, "RELATED_BUT_DISTINCT")

    def test_parent_child_handling(self) -> None:
        parent = candidate(
            "parent",
            "Vehicle washing service",
            family="vehicle_washing",
            keywords=("vehicle", "washing"),
        )
        child = candidate(
            "child",
            "Automatic vehicle washing service",
            family="vehicle_washing",
            keywords=("automatic", "vehicle", "washing"),
        )
        decision, _ = build.adjudicate_pair(parent, child, 0.8, self.rules)
        self.assertEqual(decision, "PARENT_CHILD")

    def test_mobile_versus_fixed_site_distinction(self) -> None:
        mobile = candidate(
            "mobile",
            "Mobile vehicle valeting",
            family="vehicle_washing",
            flags=("mobile",),
        )
        fixed = candidate(
            "fixed",
            "Vehicle valeting facility",
            family="vehicle_washing",
            flags=("fixed_site",),
        )
        decision, _ = build.adjudicate_pair(mobile, fixed, 0.7, self.rules)
        self.assertEqual(decision, "RELATED_BUT_DISTINCT")

    def test_alias_attachment(self) -> None:
        archetypes = [{"archetype_id": "arch_test", "alias_count": 0}]
        crosswalk = [
            {
                "archetype_id": "arch_test",
                "source": "uk_sic_2007",
                "source_record_id": "sic_1",
                "source_code": "96010",
                "source_label": "Washing and cleaning of textile and fur products",
                "mapping_confidence": 0.91,
            }
        ]
        candidates = [
            {
                "candidate_id": "candidate_1",
                "sic_code": "96010",
            }
        ]
        activity_rows = [
            {
                "sic_code": "96010",
                "activity_term": "Coin-operated laundry",
                "normalised_label": "coin-operated laundry",
                "source": "uk_sic_2007_activity_index",
                "source_record_id": "activity_1",
            }
        ]
        aliases = build.build_aliases(
            archetypes,
            crosswalk,
            candidates,
            {"candidate_1": "arch_test"},
            activity_rows,
        )
        self.assertEqual({row["alias_type"] for row in aliases}, {"official_label", "activity_index_term"})
        self.assertEqual(archetypes[0]["alias_count"], 2)

    def test_stable_deterministic_ids(self) -> None:
        first = build.stable_archetype_id(
            "Self-service laundrette", "laundry_services", ["fixed_site", "self_service"]
        )
        second = build.stable_archetype_id(
            "Self-service laundrette", "laundry_services", ["self_service", "fixed_site"]
        )
        self.assertEqual(first, second)
        self.assertEqual(
            build.canonical_key("Self-storage facility"),
            build.canonical_key("Self storage facility"),
        )

    def test_anchored_override_matches_each_official_label(self) -> None:
        canonical_name, family_id, method = build.apply_override(
            "Self Storage Facility", "Self Storage Facility", self.rules
        )
        self.assertEqual(canonical_name, "Self-storage facility")
        self.assertEqual(family_id, "storage_services")
        self.assertEqual(method, "canonical_override")

    def test_broad_category_exclusion(self) -> None:
        reason = build.exclusion_for_label("Other service activities n.e.c.", self.rules)
        self.assertIsNotNone(reason)
        self.assertEqual(reason[1], "broad_label")

    def test_family_matching_uses_words_not_substrings(self) -> None:
        family, _ = build.choose_family("Newspaper printing", self.rules)
        self.assertEqual(family["id"], "media_studios")
        family, _ = build.choose_family("Books publishing", self.rules)
        self.assertNotEqual(family["id"], "bars_and_nightlife")

    def test_operating_family_precedence(self) -> None:
        family, _ = build.choose_family("Computer repair shop", self.rules)
        self.assertEqual(family["id"], "repair_services")
        family, _ = build.choose_family(
            "Automotive Body, Paint, and Interior Repair and Maintenance", self.rules
        )
        self.assertEqual(family["id"], "vehicle_repair")
        family, _ = build.choose_family("Nursery and Tree Production", self.rules)
        self.assertEqual(family["id"], "farming")

    def test_workshop_is_not_misread_as_shop(self) -> None:
        family, domain = build.choose_family("Specialist craft workshop", self.rules)
        mechanics = build.derive_mechanics(
            "Specialist craft workshop",
            "Specialist craft workshop",
            family,
            domain,
            {"physical": True},
            self.rules,
        )
        self.assertNotIn("retail", mechanics["distinction_flags"])
        retail_family, retail_domain = build.choose_family("Garden supply retailer", self.rules)
        retail_mechanics = build.derive_mechanics(
            "Garden supply retailer",
            "Garden supply retailer",
            retail_family,
            retail_domain,
            {"physical": True},
            self.rules,
        )
        self.assertEqual(retail_mechanics["operation_type"], "retail")
        childcare_family, childcare_domain = build.choose_family("Kindergarten", self.rules)
        childcare_mechanics = build.derive_mechanics(
            "Kindergarten",
            "Kindergarten",
            childcare_family,
            childcare_domain,
            {"physical": True},
            self.rules,
        )
        self.assertEqual(childcare_mechanics["operation_type"], "childcare")

    def test_infrastructure_exclusion(self) -> None:
        reason = build.osm_exclusion_reason("amenity", "amnesty_bin", 2, self.rules)
        self.assertIsNotNone(reason)
        self.assertEqual(reason[1], "osm_non_business")

    def test_llm_disabled_unresolved_fallback(self) -> None:
        left = candidate(
            "left",
            "Specialist garment cleaning",
            keywords=("specialist", "garment", "cleaning"),
        )
        right = candidate(
            "right",
            "Garment specialist cleaners",
            keywords=("garment", "specialist", "cleaners"),
        )
        decision, _ = build.adjudicate_pair(left, right, 0.86, self.rules)
        self.assertEqual(decision, "INSUFFICIENT_INFORMATION")

    def test_repeatable_csv_output(self) -> None:
        rows = [{"id": "1", "value": "Café"}, {"id": "2", "value": None}]
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "result.csv"
            build.write_csv_atomic(path, rows, ["id", "value"])
            first = hashlib.sha256(path.read_bytes()).hexdigest()
            build.write_csv_atomic(path, rows, ["id", "value"])
            second = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(first, second)


class CatalogueValidationHelperTests(unittest.TestCase):
    def test_crosswalk_integrity(self) -> None:
        rows = [
            {
                "archetype_id": "arch_1",
                "source_record_id": "source_1",
                "relationship": "equivalent",
            }
        ]
        self.assertEqual(
            validate.crosswalk_integrity_errors(rows, {"arch_1"}, {"source_1"}), []
        )
        self.assertTrue(
            validate.crosswalk_integrity_errors(rows, {"arch_other"}, {"source_1"})
        )

    def test_hierarchy_cycle_prevention(self) -> None:
        self.assertIsNone(validate.find_hierarchy_cycle({"domain": "", "family": "domain"}))
        cycle = validate.find_hierarchy_cycle({"a": "b", "b": "c", "c": "a"})
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle[0], cycle[-1])


if __name__ == "__main__":
    unittest.main()
