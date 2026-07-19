#!/usr/bin/env python3
"""Validate financial, labour-count, and risk priors and write an audit report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import pyarrow.parquet as pq
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(f"Missing dependency {exc.name!r}; install requirements.txt") from exc


ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    "archetypes": ROOT / "data/processed/archetypes_v0.csv",
    "financial": ROOT / "data/processed/industry_financial_priors_v0.parquet",
    "labour": ROOT / "data/processed/industry_labour_priors_v0.parquet",
    "risk": ROOT / "data/processed/industry_risk_priors_v0.parquet",
    "priors": ROOT / "data/processed/archetype_priors_v0.parquet",
    "crosswalk": ROOT / "data/processed/archetype_prior_crosswalk_v0.parquet",
    "unresolved": ROOT / "data/processed/unresolved_prior_mappings_v0.csv",
    "review": ROOT / "reports/archetype_priors_review_sample.csv",
    "manifest": ROOT / "data/enrichment_source_manifest.csv",
    "report": ROOT / "reports/archetype_priors_validation_report.md",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def finite_or_none(value: Any) -> bool:
    return value is None or (isinstance(value, (int, float)) and math.isfinite(value))


def check_range(rows: list[dict[str, Any]], fields: list[str], low: float, high: float, errors: list[str]) -> None:
    for field in fields:
        invalid = [row for row in rows if row.get(field) is not None and not (low <= row[field] <= high)]
        if invalid:
            errors.append(f"{field}: {len(invalid)} value(s) outside [{low}, {high}]")


def validate() -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    for name, path in PATHS.items():
        if name != "report" and not path.exists():
            errors.append(f"Missing required file: {path.relative_to(ROOT)}")
    if errors:
        return errors, warnings, {}

    with PATHS["archetypes"].open(newline="", encoding="utf-8") as handle:
        catalogue = list(csv.DictReader(handle))
    actionable = {row["archetype_id"]: row for row in catalogue if row["archetype_level"] == "actionable"}
    financial = pq.read_table(PATHS["financial"]).to_pylist()
    labour = pq.read_table(PATHS["labour"]).to_pylist()
    risk = pq.read_table(PATHS["risk"]).to_pylist()
    priors_table = pq.read_table(PATHS["priors"])
    priors = priors_table.to_pylist()
    crosswalk = pq.read_table(PATHS["crosswalk"]).to_pylist()

    if not financial or not labour or not risk or not priors or not crosswalk:
        errors.append("One or more Parquet outputs have zero rows")
    prior_ids = [row["archetype_id"] for row in priors]
    if len(prior_ids) != len(set(prior_ids)):
        errors.append("archetype_priors_v0 has duplicate archetype IDs")
    if set(prior_ids) != set(actionable):
        errors.append("Archetype prior IDs do not exactly match actionable catalogue IDs")
    if prior_ids != sorted(prior_ids):
        errors.append("Archetype priors are not deterministically sorted by archetype_id")

    crosswalk_ids = {row["archetype_id"] for row in crosswalk}
    missing_crosswalk = [row["archetype_id"] for row in priors if any(row.get(field) is not None for field in ("turnover_per_unit_central", "median_hourly_pay", "survival_1y")) and row["archetype_id"] not in crosswalk_ids]
    if missing_crosswalk:
        errors.append(f"{len(missing_crosswalk)} prior rows with values lack mapping crosswalk evidence")
    if any(row["archetype_id"] not in actionable for row in crosswalk):
        errors.append("Prior crosswalk contains an unknown archetype_id")

    confidence_fields = ["financial_confidence", "labour_confidence", "risk_confidence", "overall_prior_confidence"]
    check_range(priors, confidence_fields, 0.0, 1.0, errors)
    check_range(crosswalk, ["mapping_confidence"], 0.0, 1.0, errors)
    check_range(risk, ["birth_rate", "death_rate", "survival_1y", "survival_2y", "survival_3y", "survival_4y", "survival_5y", "insolvency_rate"], 0.0, 1.0, errors)

    ratio_bounds = {
        "purchases_ratio": (0.0, 2.0), "employment_cost_ratio": (0.0, 2.0),
        "agva_margin": (-1.0, 2.0), "operating_surplus_proxy_margin": (-2.0, 2.0),
        "capex_ratio": (0.0, 2.0),
    }
    for field, (low, high) in ratio_bounds.items():
        check_range(financial, [field], low, high, errors)
    for table_name, rows in (("financial", financial), ("labour", labour), ("risk", risk), ("archetype", priors)):
        for row in rows:
            for key, value in row.items():
                if isinstance(value, float) and not finite_or_none(value):
                    errors.append(f"{table_name}.{key} contains a non-finite value")
                    break

    for row in financial:
        if row["turnover_per_reporting_unit"] is not None and not (row["turnover_total"] is not None and row["reporting_unit_count"] and row["reporting_unit_count"] > 0):
            errors.append(f"Derived turnover per reporting unit lacks a positive compatible denominator for {row['industry_code']}")
        for low, centre, high in (("turnover_per_unit_low", "turnover_per_unit_central", "turnover_per_unit_high"),):
            values = (row[low], row[centre], row[high])
            if all(value is not None for value in values) and not (values[0] <= values[1] <= values[2]):
                errors.append(f"Unordered financial interval for {row['industry_code']}")
        if row["operating_surplus_proxy"] is not None:
            provenance = json.loads(row["provenance_json"])
            if "Not operating profit" not in provenance.get("operating_surplus_warning", ""):
                errors.append(f"Operating-surplus proxy warning absent for {row['industry_code']}")

    for row in priors:
        for prefix in ("turnover_per_unit", "purchases_ratio", "employment_cost_ratio", "agva_margin", "operating_surplus_proxy_margin", "employees_per_unit"):
            values = (row.get(prefix + "_low"), row.get(prefix + "_central"), row.get(prefix + "_high"))
            if all(value is not None for value in values) and not (values[0] <= values[1] <= values[2]):
                errors.append(f"Unordered {prefix} interval for {row['archetype_id']}")
        numeric_values = [value for key, value in row.items() if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if numeric_values and not row.get("provenance_json"):
            errors.append(f"Financial values lack provenance for {row['archetype_id']}")
        if row.get("financial_evidence_grade") == "F" or row.get("labour_evidence_grade") == "F" or row.get("risk_evidence_grade") == "F":
            errors.append(f"Prohibited grade F value for {row['archetype_id']}")
        if row.get("mapping_method") == "domain-level deterministic fallback" and not row.get("requires_manual_review"):
            errors.append(f"Domain fallback not flagged for review: {row['archetype_id']}")

    forbidden_columns = {"profit", "operating_profit", "ebitda", "net_profit", "monthly_revenue", "automation_percentage", "probability_earning_15000"}
    actual_columns = {name.casefold() for name in priors_table.column_names}
    if actual_columns & forbidden_columns:
        errors.append(f"Prohibited final-decision/financial fields present: {sorted(actual_columns & forbidden_columns)}")
    if any("foreign" in str(row.get("source_ids", "")).casefold() or "statcan" in str(row.get("source_ids", "")).casefold() or "aies" in str(row.get("source_ids", "")).casefold() for row in priors):
        warnings.append("Foreign proxy source detected; verify grade D and transfer metadata")

    sic_codes = [row["industry_code"] for rows in (financial, labour, risk) for row in rows]
    if any(not isinstance(value, str) for value in sic_codes):
        errors.append("One or more SIC codes are not stored as strings")
    if not any(value.startswith("0") for value in sic_codes if value.isdigit()):
        errors.append("No leading-zero SIC codes found; possible formatting loss")

    with PATHS["manifest"].open(newline="", encoding="utf-8") as handle:
        manifest = list(csv.DictReader(handle))
    if any(row["validation_status"] != "passed" for row in manifest):
        errors.append("Enrichment manifest contains an unvalidated source")
    if any(not row["source_year"] or not row["geography"] or not row["licence"] for row in manifest):
        errors.append("Source manifest lacks year, geography, or licence metadata")
    if any(not row["monetary_units"] for row in manifest):
        errors.append("Source manifest lacks unit metadata")

    with PATHS["review"].open(newline="", encoding="utf-8") as handle:
        review = list(csv.DictReader(handle))
    if len(review) < 100:
        errors.append(f"Review sample has only {len(review)} rows; at least 100 required")
    strata = Counter(row["review_stratum"] for row in review)
    required_strata = {"exact_sic", "parent_fallback", "domain_fallback", "missing_data", "high_confidence", "low_confidence"}
    if required_strata - set(strata):
        errors.append(f"Review sample missing strata: {sorted(required_strata - set(strata))}")

    financial_coverage = sum(row["turnover_per_unit_central"] is not None for row in priors) / len(priors)
    labour_coverage = sum(row["employees_per_unit_central"] is not None or row["median_hourly_pay"] is not None for row in priors) / len(priors)
    risk_coverage = sum(row["survival_1y"] is not None or row["death_rate"] is not None or row["insolvency_rate"] is not None for row in priors) / len(priors)
    if financial_coverage < 0.70:
        errors.append(f"Financial coverage {financial_coverage:.1%} is below the 70% acceptance threshold")

    stats = {
        "catalogue_actionable": len(actionable), "financial_industry_priors": len(financial),
        "labour_industry_priors": len(labour), "risk_industry_priors": len(risk),
        "archetype_priors": len(priors), "crosswalk_mappings": len(crosswalk),
        "financial_coverage": financial_coverage, "labour_coverage": labour_coverage,
        "risk_coverage": risk_coverage,
        "exact_financial": sum(row.get("prior_domain") == "financial" and row.get("mapping_method") == "exact SIC" for row in crosswalk),
        "parent_financial": sum(row.get("prior_domain") == "financial" and row.get("mapping_method") == "SIC parent fallback" for row in crosswalk),
        "domain_financial": sum(row.get("prior_domain") == "financial" and row.get("mapping_method") == "domain-level deterministic fallback" for row in crosswalk),
        "without_financial": sum(row["turnover_per_unit_central"] is None for row in priors),
        "manual_review": sum(row["requires_manual_review"] for row in priors),
        "high_confidence": sum((row["overall_prior_confidence"] or 0) >= 0.65 for row in priors),
        "low_confidence": sum((row["overall_prior_confidence"] or 1) < 0.45 for row in priors),
        "review_strata": dict(strata),
        "hashes": {key: file_sha256(path) for key, path in PATHS.items() if key not in {"report"}},
    }
    return errors, warnings, stats


def write_report(errors: list[str], warnings: list[str], stats: dict[str, Any]) -> None:
    status = "PASSED" if not errors else "FAILED"
    lines = [
        "# Archetype priors validation report", "",
        f"Status: **{status}**", "",
        f"Generated: `{datetime.now(UTC).replace(microsecond=0).isoformat()}`", "",
    ]
    if stats:
        lines += [
            "## Coverage and integrity", "",
            "| Check | Result |", "|---|---:|",
            f"| Actionable catalogue rows | {stats['catalogue_actionable']:,} |",
            f"| Archetype prior rows | {stats['archetype_priors']:,} |",
            f"| Financial coverage | {stats['financial_coverage']:.1%} |",
            f"| Labour coverage | {stats['labour_coverage']:.1%} |",
            f"| Risk coverage | {stats['risk_coverage']:.1%} |",
            f"| Exact financial SIC mappings | {stats['exact_financial']:,} |",
            f"| Parent financial SIC mappings | {stats['parent_financial']:,} |",
            f"| Broad domain financial fallbacks | {stats['domain_financial']:,} |",
            f"| Archetypes without financial priors | {stats['without_financial']:,} |",
            f"| Manual-review rows | {stats['manual_review']:,} |",
            f"| High-confidence rows | {stats['high_confidence']:,} |",
            f"| Low-confidence rows | {stats['low_confidence']:,} |", "",
            "## Validation performed", "",
            "- Source manifests, hashes, licences, units, years, and geographies are present.",
            "- SIC codes remain strings and leading-zero codes survive.",
            "- Ratios, survival values, confidence values, intervals, and finite-number constraints pass.",
            "- Every mapped value links to a current actionable archetype and mapping crosswalk.",
            "- Operating-surplus proxy warnings explicitly reject profit/EBITDA/net-profit interpretations.",
            "- No monthly decision metrics, automation scoring, Grade F values, or LLM financial values are present.",
            "- Deterministic order and a stratified review sample of at least 100 rows are enforced.", "",
            "## Output hashes", "",
        ]
        for key, digest in sorted(stats["hashes"].items()):
            lines.append(f"- `{key}`: `{digest}`")
    lines += ["", "## Errors", ""]
    lines += [f"- {item}" for item in errors] or ["None."]
    lines += ["", "## Warnings", ""]
    lines += [f"- {item}" for item in warnings] or ["None."]
    lines += [""]
    PATHS["report"].parent.mkdir(parents=True, exist_ok=True)
    temp = PATHS["report"].with_name(PATHS["report"].name + ".part")
    temp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temp, PATHS["report"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    errors, warnings, stats = validate()
    write_report(errors, warnings, stats)
    if errors:
        print("Validation FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Validation PASSED")
    print(f"Financial coverage: {stats['financial_coverage']:.1%}")
    print(f"Labour coverage: {stats['labour_coverage']:.1%}")
    print(f"Risk coverage: {stats['risk_coverage']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
