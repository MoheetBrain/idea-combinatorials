#!/usr/bin/env python3
"""Independently validate the canonical business-archetype catalogue."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import pyarrow.parquet as pq
except ModuleNotFoundError as exc:  # pragma: no cover - startup guidance
    raise SystemExit(
        f"Missing dependency {exc.name!r}. Install requirements.txt before running."
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
INTERIM_DIR = REPO_ROOT / "data/interim"
PROCESSED_DIR = REPO_ROOT / "data/processed"
REPORT_PATH = REPO_ROOT / "reports/archetype_validation_report.md"

ARCHETYPE_PATH = PROCESSED_DIR / "archetypes_v0.csv"
CROSSWALK_PATH = PROCESSED_DIR / "taxonomy_crosswalk_v0.parquet"
ALIAS_PATH = PROCESSED_DIR / "archetype_aliases_v0.parquet"
UNRESOLVED_PATH = PROCESSED_DIR / "unresolved_categories_v0.csv"
EXCLUDED_PATH = PROCESSED_DIR / "excluded_categories_v0.csv"
MATCH_PATH = INTERIM_DIR / "archetype_match_candidates_v0.parquet"
REVIEW_PATH = REPO_ROOT / "reports/archetype_review_sample.csv"

SOURCE_PATHS = [
    INTERIM_DIR / "sic_hierarchy.parquet",
    INTERIM_DIR / "sic_activity_index.parquet",
    INTERIM_DIR / "voa_scat.parquet",
    INTERIM_DIR / "naics_hierarchy.parquet",
    INTERIM_DIR / "naics_descriptions.parquet",
    INTERIM_DIR / "osm_business_categories.parquet",
]

REQUIRED_ARCHETYPE_COLUMNS = [
    "archetype_id",
    "canonical_name",
    "parent_archetype_id",
    "parent_archetype_name",
    "archetype_level",
    "short_description",
    "customer_need",
    "customer_type",
    "core_physical_process",
    "revenue_unit",
    "required_assets",
    "typical_location",
    "physical_venue",
    "fixed_site_required",
    "mobile_operation_possible",
    "sic2007_codes",
    "sic2007_labels",
    "voa_scat_codes",
    "voa_scat_labels",
    "naics_codes",
    "naics_labels",
    "osm_tags",
    "osm_observed_count_london",
    "alias_count",
    "source_count",
    "source_names",
    "source_record_ids",
    "evidence_strength",
    "normalisation_method",
    "normalisation_confidence",
    "requires_manual_review",
    "provenance_json",
]

FORBIDDEN_FIELDS = {
    "revenue_estimate",
    "profit",
    "profit_margin",
    "startup_cost",
    "automation_percentage",
    "automation_potential",
    "monte_carlo",
    "survival_probability",
}
ALLOWED_LEVELS = {"domain", "business_family", "actionable"}
ALLOWED_RELATIONSHIPS = {"equivalent", "parent", "child", "example", "related"}


def normalise_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"[_/]+", " ", value)
    value = re.sub(r"[^\w&+' -]+", " ", value, flags=re.UNICODE)
    return " ".join(value.casefold().split())


def canonical_key(value: str) -> str:
    key = normalise_text(value).replace("-", " ")
    key = re.sub(r"\bcentres?\b", "center", key)
    key = re.sub(r"\bstores?\b", "shop", key)
    key = re.sub(r"\blaundromat\b|\blaunderette\b", "laundrette", key)
    key = re.sub(r"\bservices\b", "service", key)
    key = re.sub(r"\brestaurants\b", "restaurant", key)
    key = re.sub(r"\bbakeries\b", "bakery", key)
    return " ".join(key.split())


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_archetype_id(
    canonical_name: str,
    family_id: str,
    distinction_flags: Iterable[str],
    archetype_level: str = "actionable",
) -> str:
    payload = stable_json(
        [archetype_level, canonical_key(canonical_name), family_id, sorted(set(distinction_flags))]
    )
    return "arch_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def parse_json(value: str, expected_type: type = list) -> Any:
    parsed = json.loads(value)
    if not isinstance(parsed, expected_type):
        raise ValueError(f"expected {expected_type.__name__} JSON, got {type(parsed).__name__}")
    return parsed


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def find_hierarchy_cycle(parent_by_id: Mapping[str, str]) -> list[str] | None:
    for start in sorted(parent_by_id):
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current:
            if current in positions:
                return path[positions[current] :] + [current]
            if current not in parent_by_id:
                break
            positions[current] = len(path)
            path.append(current)
            current = parent_by_id[current]
    return None


def crosswalk_integrity_errors(
    crosswalk: list[dict[str, Any]],
    valid_archetype_ids: set[str],
    valid_source_record_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(crosswalk, start=1):
        if row["archetype_id"] not in valid_archetype_ids:
            errors.append(f"crosswalk row {index} has unknown archetype_id")
        if row["source_record_id"] not in valid_source_record_ids:
            errors.append(f"crosswalk row {index} has unknown source_record_id")
        if row["relationship"] not in ALLOWED_RELATIONSHIPS:
            errors.append(f"crosswalk row {index} has invalid relationship")
    return errors


def rows_are_sorted(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> bool:
    projected = [tuple("" if row.get(key) is None else str(row.get(key)) for key in keys) for row in rows]
    return projected == sorted(projected)


def audit_catalogue() -> tuple[list[tuple[str, bool, str]], dict[str, int]]:
    required_paths = [
        ARCHETYPE_PATH,
        CROSSWALK_PATH,
        ALIAS_PATH,
        UNRESOLVED_PATH,
        EXCLUDED_PATH,
        MATCH_PATH,
        REVIEW_PATH,
        *SOURCE_PATHS,
    ]
    missing = [str(path.relative_to(REPO_ROOT)) for path in required_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing required outputs or inputs: " + ", ".join(missing))

    columns, archetypes = read_csv(ARCHETYPE_PATH)
    _, unresolved = read_csv(UNRESOLVED_PATH)
    _, excluded = read_csv(EXCLUDED_PATH)
    _, review = read_csv(REVIEW_PATH)
    crosswalk = pq.read_table(CROSSWALK_PATH).to_pylist()
    aliases = pq.read_table(ALIAS_PATH).to_pylist()
    matches = pq.read_table(MATCH_PATH).to_pylist()

    source_ids: set[str] = set()
    sic_level_by_id: dict[str, str] = {}
    input_records = 0
    for path in SOURCE_PATHS:
        table = pq.read_table(path)
        input_records += table.num_rows
        ids = table.column("source_record_id").to_pylist()
        source_ids.update(ids)
        if path.name == "sic_hierarchy.parquet":
            levels = table.column("hierarchy_level").to_pylist()
            sic_level_by_id.update(zip(ids, levels, strict=True))

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    check(
        "exact archetype schema",
        columns == REQUIRED_ARCHETYPE_COLUMNS,
        f"{len(columns)} columns found",
    )
    forbidden = sorted(set(columns) & FORBIDDEN_FIELDS)
    check("no financial or automation fields", not forbidden, f"forbidden fields: {forbidden or 'none'}")
    check("positive catalogue", bool(archetypes), f"{len(archetypes):,} rows")

    ids = [row["archetype_id"] for row in archetypes]
    id_set = set(ids)
    check("unique archetype IDs", len(ids) == len(id_set), f"{len(ids) - len(id_set):,} duplicates")
    check("non-empty canonical names", all(row["canonical_name"].strip() for row in archetypes), "all rows checked")
    check("valid hierarchy levels", all(row["archetype_level"] in ALLOWED_LEVELS for row in archetypes), "domain/family/actionable only")

    actionable = [row for row in archetypes if row["archetype_level"] == "actionable"]
    parents = [row for row in archetypes if row["archetype_level"] != "actionable"]
    check("minimum actionable catalogue", len(actionable) >= 500, f"{len(actionable):,} actionable rows")
    check("actionable customer needs", all(row["customer_need"].strip() for row in actionable), "all actionable rows")
    check("actionable core processes", all(row["core_physical_process"].strip() for row in actionable), "all actionable rows")

    provenance_errors: list[str] = []
    parsed_provenance: dict[str, dict[str, Any]] = {}
    for row in archetypes:
        try:
            parsed_provenance[row["archetype_id"]] = parse_json(row["provenance_json"], dict)
        except (json.JSONDecodeError, ValueError) as exc:
            provenance_errors.append(f"{row['archetype_id']}: {exc}")
    check("valid provenance JSON", not provenance_errors, f"{len(provenance_errors):,} invalid rows")

    parent_by_id = {row["archetype_id"]: row["parent_archetype_id"] for row in archetypes}
    dangling_parents = sorted({parent for parent in parent_by_id.values() if parent and parent not in id_set})
    check("valid parent IDs", not dangling_parents, f"{len(dangling_parents):,} dangling IDs")
    cycle = find_hierarchy_cycle(parent_by_id)
    check("acyclic hierarchy", cycle is None, "no cycle" if cycle is None else " -> ".join(cycle))

    stable_id_errors: list[str] = []
    for row in archetypes:
        provenance = parsed_provenance.get(row["archetype_id"], {})
        level = row["archetype_level"]
        if level == "domain":
            family_key = provenance.get("domain_id", "")
            flags: list[str] = []
        else:
            family_key = provenance.get("family_id", "")
            flags = provenance.get("distinction_flags", []) if level == "actionable" else []
        expected = stable_archetype_id(row["canonical_name"], family_key, flags, level)
        if expected != row["archetype_id"]:
            stable_id_errors.append(row["archetype_id"])
    check("stable deterministic IDs", not stable_id_errors, f"{len(stable_id_errors):,} mismatches")

    crosswalk_errors = crosswalk_integrity_errors(crosswalk, id_set, source_ids)
    check("crosswalk referential integrity", not crosswalk_errors, f"{len(crosswalk_errors):,} errors")
    crosswalk_by_archetype: dict[str, set[str]] = defaultdict(set)
    for row in crosswalk:
        crosswalk_by_archetype[row["archetype_id"]].add(row["source_record_id"])
    missing_crosswalk = [row["archetype_id"] for row in actionable if not crosswalk_by_archetype[row["archetype_id"]]]
    check("actionable provenance mappings", not missing_crosswalk, f"{len(missing_crosswalk):,} missing mappings")

    source_list_errors = 0
    for row in actionable:
        try:
            declared = set(parse_json(row["source_record_ids"]))
        except (json.JSONDecodeError, ValueError):
            source_list_errors += 1
            continue
        if declared != crosswalk_by_archetype[row["archetype_id"]]:
            source_list_errors += 1
    check("CSV-to-crosswalk provenance equality", source_list_errors == 0, f"{source_list_errors:,} inconsistent rows")

    alias_ids = {row["archetype_id"] for row in aliases}
    alias_errors = sum(row["archetype_id"] not in id_set for row in aliases)
    check("alias referential integrity", alias_errors == 0, f"{alias_errors:,} invalid aliases")
    missing_alias = [row["archetype_id"] for row in actionable if row["archetype_id"] not in alias_ids]
    check("actionable alias coverage", not missing_alias, f"{len(missing_alias):,} actionable rows without aliases")

    confidence_errors = 0
    for row in archetypes:
        try:
            value = float(row["normalisation_confidence"])
            confidence_errors += not (0.0 <= value <= 1.0)
        except (TypeError, ValueError):
            confidence_errors += 1
    confidence_errors += sum(not 0.0 <= float(row["mapping_confidence"]) <= 1.0 for row in crosswalk)
    confidence_errors += sum(not 0.0 <= float(row["confidence"]) <= 1.0 for row in aliases)
    check("confidence bounds", confidence_errors == 0, f"{confidence_errors:,} invalid values")

    sic_sections_actionable = {
        row["source_record_id"]
        for row in crosswalk
        if sic_level_by_id.get(row["source_record_id"]) == "Section"
        and row["archetype_id"] in {item["archetype_id"] for item in actionable}
    }
    check("no actionable SIC Sections", not sic_sections_actionable, f"{len(sic_sections_actionable):,} violations")

    names_to_ids: dict[str, set[str]] = defaultdict(set)
    for row in actionable:
        names_to_ids[canonical_key(row["canonical_name"])].add(row["archetype_id"])
    distinct_pairs = [
        ("self-service laundrette", "full-service laundry"),
        ("self-service laundrette", "dry-cleaning establishment"),
        ("mobile car-valeting service", "automatic conveyor car wash"),
        ("automatic conveyor car wash", "self-service jet wash"),
    ]
    collapsed_pairs = []
    observed_pairs = 0
    for left, right in distinct_pairs:
        left_ids = names_to_ids.get(canonical_key(left), set())
        right_ids = names_to_ids.get(canonical_key(right), set())
        if left_ids and right_ids:
            observed_pairs += 1
            if left_ids & right_ids:
                collapsed_pairs.append(f"{left} / {right}")
    check("distinct operating models preserved", not collapsed_pairs, f"{observed_pairs} reference pairs observed; {len(collapsed_pairs)} collapsed")

    synthetic_pattern = re.compile(r"\b(?:ai-powered|ai enabled|robot-powered|robotic)\b", re.I)
    synthetic_rows = [row["archetype_id"] for row in archetypes if synthetic_pattern.search(row["canonical_name"])]
    synthetic_rows += [row["archetype_id"] for row in archetypes if "synthetic" in row["normalisation_method"].casefold()]
    check("no synthetic AI/robot combinations", not synthetic_rows, f"{len(set(synthetic_rows)):,} suspect rows")

    level_order = {"domain": 0, "business_family": 1, "actionable": 2}
    expected_order = sorted(
        archetypes,
        key=lambda row: (level_order[row["archetype_level"]], normalise_text(row["canonical_name"]), row["archetype_id"]),
    )
    check("deterministic archetype sort", archetypes == expected_order, "level/name/ID order")
    check("deterministic crosswalk sort", rows_are_sorted(crosswalk, ("archetype_id", "source", "source_record_id")), "archetype/source/record order")
    check("deterministic alias sort", rows_are_sorted(aliases, ("archetype_id", "normalised_alias", "source", "source_record_id")), "archetype/alias/source/record order")

    readable_parquet = all(pq.ParquetFile(path).metadata is not None for path in (CROSSWALK_PATH, ALIAS_PATH, MATCH_PATH))
    check("Parquet readability", readable_parquet, "crosswalk, aliases and match candidates")
    check("review sample coverage", len(review) >= 100, f"{len(review):,} rows")
    strata = Counter(row["review_stratum"] for row in review)
    required_strata = {
        "high_confidence_cross_source",
        "single_source",
        "high_osm_count",
        "low_confidence_mapping",
        "parent_child_case",
        "related_but_distinct",
    }
    check("review sample strata", required_strata <= set(strata), f"strata: {sorted(strata)}")

    counts = {
        "input_records": input_records,
        "archetypes": len(archetypes),
        "actionable": len(actionable),
        "parents": len(parents),
        "aliases": len(aliases),
        "crosswalk": len(crosswalk),
        "matches": len(matches),
        "excluded": len(excluded),
        "unresolved": len(unresolved),
        "manual_review": sum(row["requires_manual_review"].casefold() == "true" for row in actionable),
        "review": len(review),
    }
    return checks, counts


def write_report(checks: list[tuple[str, bool, str]], counts: Mapping[str, int]) -> None:
    passed = sum(ok for _, ok, _ in checks)
    failed = len(checks) - passed
    lines = [
        "# Archetype catalogue validation report",
        "",
        f"Validation status: **{'PASS' if failed == 0 else 'FAIL'}**",
        f"Checks passed: {passed}/{len(checks)}",
        "",
        "## Catalogue counts",
        "",
        *[f"- {name.replace('_', ' ').title()}: {value:,}" for name, value in counts.items()],
        "",
        "## Validation checks",
        "",
        "| Check | Status | Evidence |",
        "|---|---:|---|",
        *[
            f"| {name} | {'PASS' if ok else 'FAIL'} | {detail.replace('|', '/')} |"
            for name, ok, detail in checks
        ],
        "",
        "## Scope boundary",
        "",
        "This audit covers catalogue structure, provenance, hierarchy, deterministic identifiers, operating-model safeguards and readable outputs. It does not add financial, automation or synthetic-business data.",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    part = REPORT_PATH.with_name(REPORT_PATH.name + ".part")
    part.write_text("\n".join(lines), encoding="utf-8")
    os.replace(part, REPORT_PATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="Only print failures and summary")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        checks, counts = audit_catalogue()
        write_report(checks, counts)
    except Exception as exc:  # noqa: BLE001 - command-line boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    failed = [(name, detail) for name, ok, detail in checks if not ok]
    if not args.quiet:
        for name, ok, detail in checks:
            print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    elif failed:
        for name, detail in failed:
            print(f"FAIL {name}: {detail}")
    print(
        f"Validation {'passed' if not failed else 'failed'}: "
        f"{len(checks) - len(failed)}/{len(checks)} checks; "
        f"{counts['actionable']:,} actionable archetypes"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
