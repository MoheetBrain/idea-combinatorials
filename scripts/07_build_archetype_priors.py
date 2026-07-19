#!/usr/bin/env python3
"""Build evidence-labelled SIC industry priors and attach them to archetypes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(f"Missing dependency {exc.name!r}; install requirements.txt") from exc


ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "financial": ROOT / "data/interim/industry_financial_observations.parquet",
    "employment": ROOT / "data/interim/industry_employment_observations.parquet",
    "risk": ROOT / "data/interim/industry_risk_observations.parquet",
    "archetypes": ROOT / "data/processed/archetypes_v0.csv",
    "crosswalk": ROOT / "data/processed/taxonomy_crosswalk_v0.parquet",
    "sic": ROOT / "data/interim/sic_hierarchy.parquet",
}
OUTPUTS = {
    "financial": ROOT / "data/processed/industry_financial_priors_v0.parquet",
    "labour": ROOT / "data/processed/industry_labour_priors_v0.parquet",
    "risk": ROOT / "data/processed/industry_risk_priors_v0.parquet",
    "archetype": ROOT / "data/processed/archetype_priors_v0.parquet",
    "crosswalk": ROOT / "data/processed/archetype_prior_crosswalk_v0.parquet",
    "unresolved": ROOT / "data/processed/unresolved_prior_mappings_v0.csv",
    "report": ROOT / "reports/archetype_priors_report.md",
    "review": ROOT / "reports/archetype_priors_review_sample.csv",
}
BUILD_VERSION = "1"

DOMAIN_FALLBACKS = {
    "Manufacturing operations": "C", "Specialist retail": "47",
    "Professional office services": "M", "Wholesale and distribution": "46",
    "Farming and growing operations": "A", "Healthcare facilities and practices": "Q",
    "Repair and maintenance services": "95", "Education and training": "P",
    "Rental and hire services": "77", "Media and studio operations": "59",
    "Extraction and resource operations": "B", "Sports and fitness venues": "93",
    "Construction services": "F", "Restaurants and prepared-food services": "56",
    "Passenger transport": "49", "Logistics and parcel services": "52",
    "Environmental services": "38", "Entertainment venues": "93",
    "Visitor accommodation": "55", "Technology services": "62",
    "Property and facilities services": "L", "Storage services": "52",
    "Food retail": "47", "Social care services": "88", "Vehicle repair and servicing": "45",
    "Bars and nightlife": "56", "Skilled trade services": "43", "Childcare services": "88",
    "Pet services": "96", "Funeral services": "96", "Laundry and textile-care services": "96",
    "Hair and beauty services": "96", "Vehicle sales and hire": "45",
    "Vehicle washing and valeting": "45",
}


FINANCIAL_PRIOR_SCHEMA = pa.schema([
    pa.field("industry_code", pa.string()), pa.field("industry_code_level", pa.string()),
    pa.field("industry_label", pa.string()), pa.field("year", pa.int64()),
    pa.field("geography", pa.string()), pa.field("reporting_unit_count", pa.float64()),
    pa.field("enterprise_count", pa.float64()), pa.field("local_unit_count", pa.float64()),
    pa.field("turnover_total", pa.float64()), pa.field("purchases_total", pa.float64()),
    pa.field("employment_cost_total", pa.float64()), pa.field("agva_total", pa.float64()),
    pa.field("capital_expenditure_total", pa.float64()),
    pa.field("turnover_per_reporting_unit", pa.float64()), pa.field("turnover_per_enterprise", pa.float64()),
    pa.field("turnover_per_local_unit", pa.float64()), pa.field("purchases_ratio", pa.float64()),
    pa.field("employment_cost_ratio", pa.float64()), pa.field("agva_margin", pa.float64()),
    pa.field("operating_surplus_proxy", pa.float64()), pa.field("operating_surplus_proxy_margin", pa.float64()),
    pa.field("capex_ratio", pa.float64()), pa.field("turnover_per_unit_low", pa.float64()),
    pa.field("turnover_per_unit_central", pa.float64()), pa.field("turnover_per_unit_high", pa.float64()),
    pa.field("purchases_ratio_low", pa.float64()), pa.field("purchases_ratio_high", pa.float64()),
    pa.field("employment_cost_ratio_low", pa.float64()), pa.field("employment_cost_ratio_high", pa.float64()),
    pa.field("agva_margin_low", pa.float64()), pa.field("agva_margin_high", pa.float64()),
    pa.field("operating_surplus_proxy_margin_low", pa.float64()),
    pa.field("operating_surplus_proxy_margin_high", pa.float64()),
    pa.field("distribution_type", pa.string()), pa.field("distribution_parameters_json", pa.string()),
    pa.field("uncertainty_method", pa.string()), pa.field("compatibility_flag", pa.string()),
    pa.field("source_ids", pa.string()), pa.field("evidence_grade", pa.string()),
    pa.field("confidence", pa.float64()), pa.field("provenance_json", pa.string()),
])

LABOUR_PRIOR_SCHEMA = pa.schema([
    pa.field("industry_code", pa.string()), pa.field("industry_code_level", pa.string()),
    pa.field("industry_label", pa.string()), pa.field("year", pa.int64()), pa.field("geography", pa.string()),
    pa.field("enterprise_count", pa.float64()), pa.field("local_unit_count", pa.float64()),
    pa.field("employment_total", pa.float64()), pa.field("employee_total", pa.float64()),
    pa.field("full_time_total", pa.float64()), pa.field("part_time_total", pa.float64()),
    pa.field("median_hourly_pay", pa.float64()), pa.field("median_annual_pay", pa.float64()),
    pa.field("median_weekly_hours", pa.float64()), pa.field("employees_per_enterprise", pa.float64()),
    pa.field("employment_per_local_unit", pa.float64()), pa.field("employees_per_unit_low", pa.float64()),
    pa.field("employees_per_unit_central", pa.float64()), pa.field("employees_per_unit_high", pa.float64()),
    pa.field("employment_cv", pa.float64()), pa.field("pay_parent_code", pa.string()),
    pa.field("distribution_type", pa.string()), pa.field("distribution_parameters_json", pa.string()),
    pa.field("uncertainty_method", pa.string()), pa.field("compatibility_flag", pa.string()),
    pa.field("source_ids", pa.string()), pa.field("evidence_grade", pa.string()),
    pa.field("confidence", pa.float64()), pa.field("provenance_json", pa.string()),
])

RISK_PRIOR_SCHEMA = pa.schema([
    pa.field("industry_code", pa.string()), pa.field("industry_code_level", pa.string()),
    pa.field("industry_label", pa.string()), pa.field("year", pa.int64()), pa.field("geography", pa.string()),
    pa.field("birth_count", pa.float64()), pa.field("death_count", pa.float64()),
    pa.field("active_business_stock", pa.float64()), pa.field("birth_rate", pa.float64()),
    pa.field("death_rate", pa.float64()), pa.field("survival_1y", pa.float64()),
    pa.field("survival_2y", pa.float64()), pa.field("survival_3y", pa.float64()),
    pa.field("survival_4y", pa.float64()), pa.field("survival_5y", pa.float64()),
    pa.field("insolvency_count", pa.float64()), pa.field("insolvency_rate", pa.float64()),
    pa.field("insolvency_parent_code", pa.string()), pa.field("compatibility_flag", pa.string()),
    pa.field("source_ids", pa.string()), pa.field("evidence_grade", pa.string()),
    pa.field("confidence", pa.float64()), pa.field("provenance_json", pa.string()),
])

ARCHETYPE_PRIOR_FIELDS = [
    pa.field("archetype_id", pa.string()), pa.field("canonical_name", pa.string()),
    pa.field("primary_sic_code", pa.string()), pa.field("prior_granularity", pa.string()),
    pa.field("prior_year", pa.int64()), pa.field("prior_geography", pa.string()),
    pa.field("turnover_per_unit_low", pa.float64()), pa.field("turnover_per_unit_central", pa.float64()),
    pa.field("turnover_per_unit_high", pa.float64()), pa.field("purchases_ratio_low", pa.float64()),
    pa.field("purchases_ratio_central", pa.float64()), pa.field("purchases_ratio_high", pa.float64()),
    pa.field("employment_cost_ratio_low", pa.float64()), pa.field("employment_cost_ratio_central", pa.float64()),
    pa.field("employment_cost_ratio_high", pa.float64()), pa.field("agva_margin_low", pa.float64()),
    pa.field("agva_margin_central", pa.float64()), pa.field("agva_margin_high", pa.float64()),
    pa.field("operating_surplus_proxy_margin_low", pa.float64()),
    pa.field("operating_surplus_proxy_margin_central", pa.float64()),
    pa.field("operating_surplus_proxy_margin_high", pa.float64()),
    pa.field("capex_ratio_low", pa.float64()), pa.field("capex_ratio_central", pa.float64()),
    pa.field("capex_ratio_high", pa.float64()), pa.field("employees_per_unit_low", pa.float64()),
    pa.field("employees_per_unit_central", pa.float64()), pa.field("employees_per_unit_high", pa.float64()),
    pa.field("median_hourly_pay", pa.float64()), pa.field("median_annual_pay", pa.float64()),
    pa.field("survival_1y", pa.float64()), pa.field("survival_3y", pa.float64()),
    pa.field("survival_5y", pa.float64()), pa.field("death_rate", pa.float64()),
    pa.field("insolvency_rate", pa.float64()), pa.field("financial_evidence_grade", pa.string()),
    pa.field("labour_evidence_grade", pa.string()), pa.field("risk_evidence_grade", pa.string()),
    pa.field("financial_confidence", pa.float64()), pa.field("labour_confidence", pa.float64()),
    pa.field("risk_confidence", pa.float64()), pa.field("overall_prior_confidence", pa.float64()),
    pa.field("source_ids", pa.string()), pa.field("mapping_method", pa.string()),
    pa.field("mapping_notes", pa.string()), pa.field("requires_manual_review", pa.bool_()),
    pa.field("provenance_json", pa.string()),
]
ARCHETYPE_PRIOR_SCHEMA = pa.schema(ARCHETYPE_PRIOR_FIELDS)

PRIOR_CROSSWALK_SCHEMA = pa.schema([
    pa.field("archetype_id", pa.string()), pa.field("canonical_name", pa.string()),
    pa.field("prior_domain", pa.string()), pa.field("source_sic_code", pa.string()),
    pa.field("matched_industry_code", pa.string()), pa.field("mapping_granularity", pa.string()),
    pa.field("mapping_method", pa.string()), pa.field("evidence_grade", pa.string()),
    pa.field("mapping_confidence", pa.float64()), pa.field("compatibility_flag", pa.string()),
    pa.field("requires_manual_review", pa.bool_()), pa.field("mapping_notes", pa.string()),
    pa.field("provenance_json", pa.string()),
])

UNRESOLVED_FIELDS = ["archetype_id", "canonical_name", "sic_codes", "reason", "best_available_level", "requires_manual_review"]
REVIEW_FIELDS = ["review_stratum", "archetype_id", "canonical_name", "primary_sic_code", "prior_granularity", "turnover_per_unit_central", "employees_per_unit_central", "survival_5y", "financial_confidence", "labour_confidence", "risk_confidence", "requires_manual_review", "mapping_notes"]


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def level(code: str | None) -> str | None:
    if not code:
        return None
    if code.isalpha():
        return "section"
    return {2: "division", 3: "group", 4: "class", 5: "subclass"}.get(len(code))


def div(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None


def bounded_ratio(numerator: float | None, denominator: float | None, numerator_cv: float | None, denominator_cv: float | None) -> tuple[float | None, float | None]:
    if numerator is None or denominator is None or denominator <= 0 or numerator_cv is None or denominator_cv is None:
        return None, None
    low_den = denominator * max(0.01, 1 - 2 * denominator_cv)
    high_den = denominator * (1 + 2 * denominator_cv)
    error = abs(numerator) * 2 * numerator_cv
    low_num, high_num = numerator - error, numerator + error
    candidates = [low_num / low_den, low_num / high_den, high_num / low_den, high_num / high_den]
    return min(candidates), max(candidates)


def parse_band(text: str) -> tuple[float, float | None]:
    clean = text.replace(",", "").strip()
    if clean.endswith("+"):
        return float(clean[:-1]), None
    left, right = clean.split("-", 1)
    return float(left), float(right)


def binned_quantile(bands: list[dict[str, Any]], quantile: float, scale: float = 1.0) -> float | None:
    usable = [(parse_band(row["turnover_size_band"]), row["band_count"]) for row in bands if row.get("turnover_size_band") and row.get("band_count") is not None and row["band_count"] >= 0]
    total = sum(count for _, count in usable)
    if total <= 0:
        return None
    target, cumulative = total * quantile, 0.0
    for (low, high), count in usable:
        if cumulative + count >= target:
            if high is None:
                return None
            fraction = 0.5 if count == 0 else (target - cumulative) / count
            return (low + fraction * (high - low)) * scale
        cumulative += count
    return None


def closest(code: str, mapping: dict[str, dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None, int]:
    candidates = [code]
    if code.isdigit():
        candidates += [code[:width] for width in (4, 3, 2) if len(code) > width]
    for distance, candidate in enumerate(dict.fromkeys(candidates)):
        if candidate in mapping:
            return candidate, mapping[candidate], distance
    return None, None, 99


def sic_context() -> tuple[dict[str, str], dict[str, str]]:
    rows = pq.read_table(INPUTS["sic"]).to_pylist()
    labels = {row["source_code"]: row["source_label"] for row in rows if row.get("source_code")}
    parents = {row["source_code"]: row.get("parent_code") for row in rows if row.get("source_code")}
    sections: dict[str, str] = {}
    for code in labels:
        cursor, seen = code, set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            if cursor.isalpha():
                sections[code] = cursor
                break
            cursor = parents.get(cursor)
    division_sections = {key: value for key, value in sections.items() if len(key) == 2 and key.isdigit()}
    for code in labels:
        if code.isdigit() and code not in sections:
            sections[code] = division_sections.get(code[:2], "")
    return labels, sections


def aggregate_counts(fin_obs: list[dict[str, Any]], field: str) -> dict[str, float]:
    base = {row["industry_code"]: row[field] for row in fin_obs if row["source_id"] == "uk_business_2025" and row.get(field) is not None and len(row["industry_code"]) == 4}
    result = dict(base)
    for width in (3, 2):
        groups: dict[str, float] = defaultdict(float)
        for code, value in base.items():
            groups[code[:width]] += value
        result.update(groups)
    return result


def build_financial_priors(fin_obs: list[dict[str, Any]], labels: dict[str, str]) -> list[dict[str, Any]]:
    abs_rows = {row["industry_code"]: row for row in fin_obs if row["source_id"] == "abs_2024_sections"}
    enterprises = aggregate_counts(fin_obs, "enterprise_count")
    local_units = aggregate_counts(fin_obs, "local_unit_count")
    bands: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fin_obs:
        if row["source_id"] == "uk_business_2025" and row.get("turnover_size_band") and row["population_basis"].startswith("enterprises"):
            bands[row["industry_code"]].append(row)
    priors = []
    for code, row in abs_rows.items():
        turnover, purchases = row["turnover_total_gbp"], row["purchases_total_gbp"]
        employment_cost, agva, capex = row["employment_cost_total_gbp"], row["agva_total_gbp"], row["capital_expenditure_total_gbp"]
        reporting = row["reporting_unit_count"]
        central = div(turnover, reporting)
        low = binned_quantile(bands.get(code, []), 0.10, 1000)
        high = binned_quantile(bands.get(code, []), 0.90, 1000)
        distribution_type = "empirical_binned" if bands.get(code) else None
        parameters = stable_json({"source": "UK Business 2025 Table 9", "bands": [{"band_gbp_thousands": x["turnover_size_band"], "count": x["band_count"]} for x in bands.get(code, [])]}) if bands.get(code) else None
        uncertainty = "Interpolated P10/P90 within observed closed turnover bands; open-ended quantiles remain null. Central estimate is ABS turnover/reporting units."
        if not bands.get(code) and central is not None and row.get("turnover_cv") is not None:
            low = central * max(0, 1 - 2 * row["turnover_cv"])
            high = central * (1 + 2 * row["turnover_cv"])
            distribution_type = "sampling_error_interval"
            parameters = stable_json({"turnover_cv": row["turnover_cv"], "multiplier": 2})
            uncertainty = "Central estimate plus/minus twice the published ABS turnover CV; no business-level distribution inferred."
        if central is not None:
            low = min(low, central) if low is not None else None
            high = max(high, central) if high is not None else None
        purchases_low, purchases_high = bounded_ratio(purchases, turnover, row.get("purchases_cv"), row.get("turnover_cv"))
        labour_low, labour_high = bounded_ratio(employment_cost, turnover, row.get("employment_cost_cv"), row.get("turnover_cv"))
        agva_low, agva_high = bounded_ratio(agva, turnover, row.get("agva_cv"), row.get("turnover_cv"))
        surplus_margin = div((agva - employment_cost) if agva is not None and employment_cost is not None else None, turnover)
        surplus_low = agva_low - labour_high if agva_low is not None and labour_high is not None else None
        surplus_high = agva_high - labour_low if agva_high is not None and labour_low is not None else None
        if surplus_margin is not None:
            surplus_low = min(x for x in (surplus_low, surplus_margin) if x is not None)
            surplus_high = max(x for x in (surplus_high, surplus_margin) if x is not None)
        cv_values = [row.get(x) for x in ("turnover_cv", "purchases_cv", "employment_cost_cv", "agva_cv") if row.get(x) is not None]
        confidence = max(0.35, min(0.95, 0.88 - (sum(cv_values) / len(cv_values) if cv_values else 0.25)))
        source_ids = ["abs_2024_sections", "abs_2024_quality"] + (["uk_business_2025"] if code in enterprises or code in local_units else [])
        priors.append({
            "industry_code": code, "industry_code_level": row["industry_code_level"], "industry_label": row["industry_label"] or labels.get(code),
            "year": 2024, "geography": "United Kingdom", "reporting_unit_count": reporting,
            "enterprise_count": enterprises.get(code), "local_unit_count": local_units.get(code),
            "turnover_total": turnover, "purchases_total": purchases, "employment_cost_total": employment_cost,
            "agva_total": agva, "capital_expenditure_total": capex,
            "turnover_per_reporting_unit": central, "turnover_per_enterprise": div(turnover, enterprises.get(code)),
            "turnover_per_local_unit": div(turnover, local_units.get(code)), "purchases_ratio": div(purchases, turnover),
            "employment_cost_ratio": div(employment_cost, turnover), "agva_margin": div(agva, turnover),
            "operating_surplus_proxy": (agva - employment_cost) if agva is not None and employment_cost is not None else None,
            "operating_surplus_proxy_margin": surplus_margin, "capex_ratio": div(capex, turnover),
            "turnover_per_unit_low": low, "turnover_per_unit_central": central, "turnover_per_unit_high": high,
            "purchases_ratio_low": purchases_low, "purchases_ratio_high": purchases_high,
            "employment_cost_ratio_low": labour_low, "employment_cost_ratio_high": labour_high,
            "agva_margin_low": agva_low, "agva_margin_high": agva_high,
            "operating_surplus_proxy_margin_low": surplus_low, "operating_surplus_proxy_margin_high": surplus_high,
            "distribution_type": distribution_type, "distribution_parameters_json": parameters,
            "uncertainty_method": uncertainty,
            "compatibility_flag": "reporting-unit derivations same source/year; enterprise/local-unit derivations combine ABS 2024 with UK Business 2025",
            "source_ids": stable_json(sorted(set(source_ids))), "evidence_grade": "B", "confidence": confidence,
            "provenance_json": stable_json({"build_version": BUILD_VERSION, "formulas": {"turnover_per_reporting_unit": "turnover_total/reporting_unit_count", "purchases_ratio": "purchases_total/turnover_total", "employment_cost_ratio": "employment_cost_total/turnover_total", "agva_margin": "agva_total/turnover_total", "operating_surplus_proxy": "agva_total-employment_cost_total", "capex_ratio": "capital_expenditure_total/turnover_total"}, "operating_surplus_warning": "Not operating profit, EBITDA, or net profit", "source_observation_id": row["observation_id"]}),
        })
    return sorted(priors, key=lambda row: (len(row["industry_code"]), row["industry_code"]))


def build_labour_priors(emp_obs: list[dict[str, Any]], fin_obs: list[dict[str, Any]], labels: dict[str, str], sections: dict[str, str]) -> list[dict[str, Any]]:
    bres = {row["industry_code"]: row for row in emp_obs if row["source_id"] == "bres_2024_provisional"}
    original_bres = dict(bres)
    # Aggregate detailed BRES five-digit estimates to four-digit classes. Suppressed children remain absent.
    aggregates: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    aggregate_counts_seen: dict[str, int] = defaultdict(int)
    for code, row in bres.items():
        if len(code) != 5:
            continue
        prefix = code[:4]
        aggregate_counts_seen[prefix] += 1
        for field in ("employment_total", "employee_total", "full_time_total", "part_time_total"):
            if row.get(field) is not None:
                aggregates[prefix][field] += row[field]
    for code, values in aggregates.items():
        bres[code] = {"industry_code": code, "industry_code_level": "class", "industry_label": labels.get(code), "year": 2024, "geography": "Great Britain", "source_id": "bres_2024_provisional", "observation_id": None, "employment_cv": None, **values}
    enterprises = aggregate_counts(fin_obs, "enterprise_count")
    local_units = aggregate_counts(fin_obs, "local_unit_count")
    for field_map in (enterprises, local_units):
        section_totals: dict[str, float] = defaultdict(float)
        for industry_code, value in list(field_map.items()):
            if len(industry_code) == 2:
                section = sections.get(industry_code)
                if section:
                    section_totals[section] += value
        field_map.update(section_totals)
    section_labour: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for industry_code, row in original_bres.items():
        if len(industry_code) != 2:
            continue
        section = sections.get(industry_code)
        if not section:
            continue
        for field in ("employment_total", "employee_total", "full_time_total", "part_time_total"):
            if row.get(field) is not None:
                section_labour[section][field] += row[field]
    for section, values in section_labour.items():
        bres[section] = {"industry_code": section, "industry_code_level": "section", "industry_label": labels.get(section), "year": 2024, "geography": "Great Britain", "source_id": "bres_2024_provisional", "observation_id": None, "employment_cv": None, **values}
    pay = {row["industry_code"]: row for row in emp_obs if row["source_id"] == "ashe_2025_table5" and row["geography"].strip().casefold() == "united kingdom" and row["industry_code"].isdigit()}
    section_pay = {row["industry_code"]: row for row in emp_obs if row["source_id"] == "ashe_2025_table29" and row.get("occupation_code") is None}
    priors = []
    for code, row in bres.items():
        pay_code = code[:2] if code.isdigit() else code
        pay_row = pay.get(pay_code or "") if code.isdigit() else section_pay.get(code)
        local = local_units.get(code)
        enterprise = enterprises.get(code)
        central = div(row.get("employment_total"), local)
        cv = row.get("employment_cv")
        low = central * max(0, 1 - 2 * cv / 100) if central is not None and cv is not None else None
        high = central * (1 + 2 * cv / 100) if central is not None and cv is not None else None
        if central is not None:
            low = min(low, central) if low is not None else None
            high = max(high, central) if high is not None else None
        compatibility = "BRES 2024 Great Britain numerator divided by UK Business 2025 United Kingdom denominator; adjacent-year/geography mismatch"
        sources = ["bres_2024_provisional", "bres_2024_cv"] + (["uk_business_2025"] if local or enterprise else []) + (["ashe_2025_table5"] if pay_row else [])
        confidence = 0.72 if central is not None else 0.60
        if cv is not None:
            confidence = max(0.3, confidence - min(cv / 100, 0.35))
        if (len(code) == 4 and code in aggregates) or code.isalpha():
            confidence -= 0.08
        priors.append({
            "industry_code": code, "industry_code_level": level(code), "industry_label": row.get("industry_label") or labels.get(code),
            "year": 2024, "geography": "Great Britain (employment); United Kingdom (denominators/pay)",
            "enterprise_count": enterprise, "local_unit_count": local, "employment_total": row.get("employment_total"),
            "employee_total": row.get("employee_total"), "full_time_total": row.get("full_time_total"), "part_time_total": row.get("part_time_total"),
            "median_hourly_pay": pay_row.get("median_hourly_pay_gbp") if pay_row else None,
            "median_annual_pay": pay_row.get("median_annual_pay_gbp") if pay_row else None,
            "median_weekly_hours": pay_row.get("median_weekly_hours") if pay_row else None,
            "employees_per_enterprise": div(row.get("employee_total"), enterprise), "employment_per_local_unit": central,
            "employees_per_unit_low": low, "employees_per_unit_central": central, "employees_per_unit_high": high,
            "employment_cv": cv, "pay_parent_code": pay_code if pay_row else None,
            "distribution_type": "sampling_error_interval" if cv is not None and central is not None else None,
            "distribution_parameters_json": stable_json({"employment_cv_percent": cv, "multiplier": 2}) if cv is not None and central is not None else None,
            "uncertainty_method": "Plus/minus twice BRES employment CV; denominator sampling/rounding uncertainty is not quantified." if cv is not None else "Point estimate only; no defensible combined uncertainty interval.",
            "compatibility_flag": compatibility, "source_ids": stable_json(sorted(set(sources))), "evidence_grade": "B" if len(code) in {2, 3, 5} else "C",
            "confidence": max(0.2, confidence),
            "provenance_json": stable_json({"build_version": BUILD_VERSION, "formulas": {"employment_per_local_unit": "BRES employment_total/UK Business local_unit_count", "employees_per_enterprise": "BRES employee_total/UK Business enterprise_count"}, "four_digit_aggregation": code in aggregates, "section_aggregation": code.isalpha(), "aggregated_child_rows": aggregate_counts_seen.get(code), "source_observation_id": row.get("observation_id")}),
        })
    return sorted(priors, key=lambda row: (len(row["industry_code"]), row["industry_code"]))


def build_risk_priors(risk_obs: list[dict[str, Any]], sections: dict[str, str], labels: dict[str, str]) -> list[dict[str, Any]]:
    demography = {row["industry_code"]: row for row in risk_obs if row["source_id"] == "business_demography_2024"}
    insolvency_rates = {row["industry_code"]: row for row in risk_obs if row["source_id"] == "business_insolvency_demography_2015_2025"}
    current_counts = {row["industry_code"]: row for row in risk_obs if row["source_id"] == "company_insolvencies_industry_2026_06"}
    priors = []
    for code, row in demography.items():
        section = sections.get(code)
        insolvency = insolvency_rates.get(section or "")
        current = current_counts.get(code)
        sources = ["business_demography_2024"] + (["business_insolvency_demography_2015_2025"] if insolvency else []) + (["company_insolvencies_industry_2026_06"] if current else [])
        confidence = 0.82 if len(code) == 3 else 0.76
        if insolvency:
            confidence -= 0.10
        priors.append({
            "industry_code": code, "industry_code_level": level(code), "industry_label": row.get("industry_label") or labels.get(code),
            "year": 2024, "geography": "United Kingdom (demography); England and Wales (insolvency)",
            "birth_count": row.get("birth_count"), "death_count": row.get("death_count"), "active_business_stock": row.get("active_business_stock"),
            "birth_rate": row.get("birth_rate"), "death_rate": row.get("death_rate"), "survival_1y": row.get("survival_1y"),
            "survival_2y": row.get("survival_2y"), "survival_3y": row.get("survival_3y"), "survival_4y": row.get("survival_4y"),
            "survival_5y": row.get("survival_5y"), "insolvency_count": current.get("insolvency_count") if current else None,
            "insolvency_rate": div(insolvency.get("insolvency_rate_per_10000"), 10_000) if insolvency else None,
            "insolvency_parent_code": section if insolvency else None,
            "compatibility_flag": "Demography is UK 2024; survival uses 2019 birth cohort; insolvency rate is section-level England and Wales 2025." if insolvency else "UK 2024 demography; survival uses 2019 birth cohort.",
            "source_ids": stable_json(sources), "evidence_grade": "B", "confidence": confidence,
            "provenance_json": stable_json({"build_version": BUILD_VERSION, "death_rate_formula": "death_count/active_business_stock", "insolvency_rate_conversion": "published per-10,000 rate divided by 10,000", "survival_cohort_year": row.get("survival_cohort_year"), "source_observation_id": row["observation_id"], "insolvency_observation_id": insolvency.get("observation_id") if insolvency else None}),
        })
    section_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for code, row in demography.items():
        if len(code) == 2 and sections.get(code):
            section_groups[sections[code]].append(row)
    for section, rows in section_groups.items():
        def total(field: str) -> float | None:
            values = [row[field] for row in rows if row.get(field) is not None]
            return sum(values) if values else None
        births, deaths, active = total("birth_count"), total("death_count"), total("active_business_stock")
        weights = [row.get("birth_count") or 0 for row in rows]
        def weighted(field: str) -> float | None:
            pairs = [(row.get(field), weight) for row, weight in zip(rows, weights) if row.get(field) is not None and weight > 0]
            denominator = sum(weight for _, weight in pairs)
            return sum(value * weight for value, weight in pairs) / denominator if denominator else None
        insolvency = insolvency_rates.get(section)
        current = current_counts.get(section)
        priors.append({
            "industry_code": section, "industry_code_level": "section", "industry_label": labels.get(section),
            "year": 2024, "geography": "United Kingdom (demography); England and Wales (insolvency)",
            "birth_count": births, "death_count": deaths, "active_business_stock": active,
            "birth_rate": div(births, active), "death_rate": div(deaths, active),
            "survival_1y": weighted("survival_1y"), "survival_2y": weighted("survival_2y"),
            "survival_3y": weighted("survival_3y"), "survival_4y": weighted("survival_4y"), "survival_5y": weighted("survival_5y"),
            "insolvency_count": current.get("insolvency_count") if current else None,
            "insolvency_rate": div(insolvency.get("insolvency_rate_per_10000"), 10_000) if insolvency else None,
            "insolvency_parent_code": section if insolvency else None,
            "compatibility_flag": "Section aggregate from UK SIC divisions; survival weighted by 2019 birth counts; insolvency rate is England and Wales 2025.",
            "source_ids": stable_json(["business_demography_2024", "business_insolvency_demography_2015_2025", "company_insolvencies_industry_2026_06"]),
            "evidence_grade": "B", "confidence": 0.68,
            "provenance_json": stable_json({"build_version": BUILD_VERSION, "aggregation": "sum division counts and birth-weight survival rates", "component_divisions": sorted(row["industry_code"] for row in rows)}),
        })
    return sorted(priors, key=lambda row: (len(row["industry_code"]), row["industry_code"]))


def degrade(base_grade: str, distance: int, domain_fallback: bool = False) -> str:
    if domain_fallback:
        return "D"
    return "B" if distance == 0 and base_grade == "B" else "C"


def map_archetypes(fin_priors: list[dict[str, Any]], labour_priors: list[dict[str, Any]], risk_priors: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    fin_map = {row["industry_code"]: row for row in fin_priors}
    labour_map = {row["industry_code"]: row for row in labour_priors}
    risk_map = {row["industry_code"]: row for row in risk_priors}
    with INPUTS["archetypes"].open(newline="", encoding="utf-8") as handle:
        archetypes = [row for row in csv.DictReader(handle) if row["archetype_level"] == "actionable"]
    crosswalk_rows = pq.read_table(INPUTS["crosswalk"]).to_pylist()
    sic_codes: dict[str, list[str]] = defaultdict(list)
    for row in crosswalk_rows:
        value = row.get("source_code")
        if row.get("source") == "uk_sic_2007" and value and str(value).isdigit():
            sic_codes[row["archetype_id"]].append(str(value))
    result, mappings, unresolved = [], [], []
    for archetype in archetypes:
        codes = sorted(set(sic_codes.get(archetype["archetype_id"], [])), key=lambda x: (-len(x), x))
        domain_code = DOMAIN_FALLBACKS.get(archetype["parent_archetype_name"])
        domain_fallback = not codes and bool(domain_code)
        candidate_codes = codes or ([domain_code] if domain_code else [])
        selected: dict[str, tuple[str | None, dict[str, Any] | None, int]] = {}
        for domain, mapping in (("financial", fin_map), ("labour", labour_map), ("risk", risk_map)):
            options = []
            for candidate in candidate_codes:
                matched_code, prior, distance = closest(candidate, mapping)
                if prior:
                    options.append((distance, -len(matched_code or ""), candidate, matched_code, prior))
            if options:
                distance, _, source_code, matched_code, prior = sorted(options, key=lambda item: item[:4])[0]
                selected[domain] = (matched_code, prior, distance)
                grade = degrade(prior["evidence_grade"], distance, domain_fallback)
                confidence = prior["confidence"] * (0.85 ** distance) * (0.55 if domain_fallback else 1.0)
                manual = archetype["requires_manual_review"].casefold() == "true" or domain_fallback or len(codes) > 1
                mappings.append({
                    "archetype_id": archetype["archetype_id"], "canonical_name": archetype["canonical_name"], "prior_domain": domain,
                    "source_sic_code": source_code, "matched_industry_code": matched_code, "mapping_granularity": level(matched_code),
                    "mapping_method": "domain-level deterministic fallback" if domain_fallback else ("exact SIC" if distance == 0 else "SIC parent fallback"),
                    "evidence_grade": grade, "mapping_confidence": confidence,
                    "compatibility_flag": prior.get("compatibility_flag"), "requires_manual_review": manual,
                    "mapping_notes": f"Selected {matched_code} from candidate {source_code}; {len(codes)} explicit SIC mapping(s).",
                    "provenance_json": stable_json({"build_version": BUILD_VERSION, "candidate_sic_codes": codes, "parent_archetype_name": archetype["parent_archetype_name"], "domain_fallback_code": domain_code if domain_fallback else None}),
                })
        fin_entry = selected.get("financial")
        labour_entry = selected.get("labour")
        risk_entry = selected.get("risk")
        fin = fin_entry[1] if fin_entry else None
        labour = labour_entry[1] if labour_entry else None
        risk = risk_entry[1] if risk_entry else None
        fin_grade = degrade(fin["evidence_grade"], fin_entry[2], domain_fallback) if fin and fin_entry else None
        labour_grade = degrade(labour["evidence_grade"], labour_entry[2], domain_fallback) if labour and labour_entry else None
        risk_grade = degrade(risk["evidence_grade"], risk_entry[2], domain_fallback) if risk and risk_entry else None
        fin_conf = fin["confidence"] * (0.85 ** fin_entry[2]) * (0.55 if domain_fallback else 1.0) if fin and fin_entry else None
        labour_conf = labour["confidence"] * (0.85 ** labour_entry[2]) * (0.55 if domain_fallback else 1.0) if labour and labour_entry else None
        risk_conf = risk["confidence"] * (0.85 ** risk_entry[2]) * (0.55 if domain_fallback else 1.0) if risk and risk_entry else None
        confidences = [x for x in (fin_conf, labour_conf, risk_conf) if x is not None]
        primary = fin_entry[0] if fin_entry else (labour_entry[0] if labour_entry else (risk_entry[0] if risk_entry else None))
        manual = archetype["requires_manual_review"].casefold() == "true" or domain_fallback or len(codes) > 1 or not fin
        methods = sorted(set(row["mapping_method"] for row in mappings if row["archetype_id"] == archetype["archetype_id"]))
        notes = []
        if len(codes) > 1:
            notes.append("Multiple SIC mappings exist; the strongest deterministic match was selected without averaging distinct industries.")
        if domain_fallback:
            notes.append(f"No explicit SIC mapping; used broad parent-family fallback {domain_code} with grade D.")
        if not candidate_codes:
            notes.append("No explicit SIC or defensible configured domain fallback.")
        if fin:
            notes.append("Turnover per unit means ABS turnover per reporting unit, not site-specific revenue.")
        row = {field.name: None for field in ARCHETYPE_PRIOR_SCHEMA}
        row.update({
            "archetype_id": archetype["archetype_id"], "canonical_name": archetype["canonical_name"], "primary_sic_code": primary,
            "prior_granularity": level(primary), "prior_year": fin.get("year") if fin else (labour.get("year") if labour else (risk.get("year") if risk else None)),
            "prior_geography": fin.get("geography") if fin else (labour.get("geography") if labour else (risk.get("geography") if risk else None)),
            "turnover_per_unit_low": fin.get("turnover_per_unit_low") if fin else None,
            "turnover_per_unit_central": fin.get("turnover_per_unit_central") if fin else None,
            "turnover_per_unit_high": fin.get("turnover_per_unit_high") if fin else None,
            "purchases_ratio_low": fin.get("purchases_ratio_low") if fin else None, "purchases_ratio_central": fin.get("purchases_ratio") if fin else None,
            "purchases_ratio_high": fin.get("purchases_ratio_high") if fin else None,
            "employment_cost_ratio_low": fin.get("employment_cost_ratio_low") if fin else None,
            "employment_cost_ratio_central": fin.get("employment_cost_ratio") if fin else None,
            "employment_cost_ratio_high": fin.get("employment_cost_ratio_high") if fin else None,
            "agva_margin_low": fin.get("agva_margin_low") if fin else None, "agva_margin_central": fin.get("agva_margin") if fin else None,
            "agva_margin_high": fin.get("agva_margin_high") if fin else None,
            "operating_surplus_proxy_margin_low": fin.get("operating_surplus_proxy_margin_low") if fin else None,
            "operating_surplus_proxy_margin_central": fin.get("operating_surplus_proxy_margin") if fin else None,
            "operating_surplus_proxy_margin_high": fin.get("operating_surplus_proxy_margin_high") if fin else None,
            "capex_ratio_central": fin.get("capex_ratio") if fin else None,
            "employees_per_unit_low": labour.get("employees_per_unit_low") if labour else None,
            "employees_per_unit_central": labour.get("employees_per_unit_central") if labour else None,
            "employees_per_unit_high": labour.get("employees_per_unit_high") if labour else None,
            "median_hourly_pay": labour.get("median_hourly_pay") if labour else None, "median_annual_pay": labour.get("median_annual_pay") if labour else None,
            "survival_1y": risk.get("survival_1y") if risk else None, "survival_3y": risk.get("survival_3y") if risk else None,
            "survival_5y": risk.get("survival_5y") if risk else None, "death_rate": risk.get("death_rate") if risk else None,
            "insolvency_rate": risk.get("insolvency_rate") if risk else None,
            "financial_evidence_grade": fin_grade, "labour_evidence_grade": labour_grade, "risk_evidence_grade": risk_grade,
            "financial_confidence": fin_conf, "labour_confidence": labour_conf, "risk_confidence": risk_conf,
            "overall_prior_confidence": sum(confidences) / len(confidences) if confidences else None,
            "source_ids": stable_json(sorted(set(sum((json.loads(x[1]["source_ids"]) for x in selected.values()), [])))) if selected else "[]",
            "mapping_method": "; ".join(methods) if methods else "unresolved", "mapping_notes": " ".join(notes),
            "requires_manual_review": manual,
            "provenance_json": stable_json({"build_version": BUILD_VERSION, "archetype_source_sic_codes": codes, "selected_prior_codes": {key: value[0] for key, value in selected.items()}, "no_llm_values": True}),
        })
        result.append(row)
        if not fin or not labour or not risk or len(codes) > 1:
            reasons = []
            if not fin: reasons.append("no defensible financial prior")
            if not labour: reasons.append("no defensible labour prior")
            if not risk: reasons.append("no defensible risk prior")
            if len(codes) > 1: reasons.append("multiple economically potentially distinct SIC mappings")
            unresolved.append({"archetype_id": archetype["archetype_id"], "canonical_name": archetype["canonical_name"], "sic_codes": stable_json(codes), "reason": "; ".join(reasons), "best_available_level": level(primary) or "none", "requires_manual_review": "true"})
    result.sort(key=lambda row: row["archetype_id"])
    mappings.sort(key=lambda row: (row["archetype_id"], row["prior_domain"]))
    unresolved.sort(key=lambda row: row["archetype_id"])
    return result, mappings, unresolved


def atomic_parquet(records: list[dict[str, Any]], schema: pa.Schema, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    pq.write_table(pa.Table.from_pylist(records, schema=schema), temp, compression="zstd", use_dictionary=True)
    if pq.ParquetFile(temp).metadata.num_rows != len(records):
        raise RuntimeError(f"Failed Parquet audit for {path}")
    os.replace(temp, path)


def atomic_csv(records: list[dict[str, Any]], fields: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(records); handle.flush(); os.fsync(handle.fileno())
    os.replace(temp, path)


def review_sample(priors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strata = {
        "exact_sic": [r for r in priors if r["mapping_method"] == "exact SIC"],
        "parent_fallback": [r for r in priors if "SIC parent fallback" in r["mapping_method"]],
        "domain_fallback": [r for r in priors if "domain-level" in r["mapping_method"]],
        "missing_data": [r for r in priors if r["turnover_per_unit_central"] is None or r["employees_per_unit_central"] is None or r["survival_5y"] is None],
        "high_confidence": [r for r in priors if (r["overall_prior_confidence"] or 0) >= 0.65],
        "low_confidence": [r for r in priors if (r["overall_prior_confidence"] or 1) < 0.45],
    }
    sample, used = [], set()
    for stratum, rows in strata.items():
        for row in sorted(rows, key=lambda x: hashlib.sha256((stratum + x["archetype_id"]).encode()).hexdigest()):
            if len([x for x in sample if x["review_stratum"] == stratum]) >= 20:
                break
            key = (stratum, row["archetype_id"])
            if key in used: continue
            used.add(key)
            sample.append({field: (stratum if field == "review_stratum" else row.get(field)) for field in REVIEW_FIELDS})
    return sample


def write_report(fin_obs_count: int, emp_obs_count: int, risk_obs_count: int, fin: list[dict[str, Any]], labour: list[dict[str, Any]], risk: list[dict[str, Any]], priors: list[dict[str, Any]], mappings: list[dict[str, Any]], unresolved: list[dict[str, str]], command: str) -> None:
    financial_coverage = sum(row["turnover_per_unit_central"] is not None for row in priors)
    labour_coverage = sum(row["employees_per_unit_central"] is not None or row["median_hourly_pay"] is not None for row in priors)
    risk_coverage = sum(row["survival_1y"] is not None or row["death_rate"] is not None or row["insolvency_rate"] is not None for row in priors)
    exact = sum(row["prior_domain"] == "financial" and row["mapping_method"] == "exact SIC" for row in mappings)
    parent = sum(row["prior_domain"] == "financial" and row["mapping_method"] == "SIC parent fallback" for row in mappings)
    domain = sum(row["prior_domain"] == "financial" and row["mapping_method"] == "domain-level deterministic fallback" for row in mappings)
    lines = [
        "# Archetype financial, labour-count, and risk priors", "",
        "These are broad statistical priors, not claims about a particular site or operator.", "",
        "## Coverage", "",
        "| Measure | Count | Coverage |", "|---|---:|---:|",
        f"| Actionable archetypes | {len(priors):,} | 100.0% |",
        f"| Financial prior | {financial_coverage:,} | {financial_coverage / len(priors):.1%} |",
        f"| Labour prior | {labour_coverage:,} | {labour_coverage / len(priors):.1%} |",
        f"| Risk prior | {risk_coverage:,} | {risk_coverage / len(priors):.1%} |", "",
        "## Mapping", "",
        f"- Exact SIC mappings: {exact:,}", f"- Parent SIC fallbacks: {parent:,}",
        f"- Broad domain fallbacks (grade D, manual review): {domain:,}",
        f"- Unresolved review rows: {len(unresolved):,}", "",
        "## Industry tables", "",
        f"- Raw financial observations: {fin_obs_count:,}; financial priors: {len(fin):,}",
        f"- Raw employment observations: {emp_obs_count:,}; labour priors: {len(labour):,}",
        f"- Raw risk observations: {risk_obs_count:,}; risk priors: {len(risk):,}",
        f"- Archetype-prior mappings: {len(mappings):,}", "",
        "## Derivation safeguards", "",
        "- `turnover_per_reporting_unit = ABS turnover / ABS reporting-unit count`; it is not site revenue.",
        "- `operating_surplus_proxy = aGVA - employment costs`; it is explicitly not operating profit, EBITDA, or net profit.",
        "- Enterprise/local-unit ratios combine ABS 2024 with UK Business 2025 only where shown, with an explicit compatibility flag and confidence penalty.",
        "- Employment per local unit combines BRES 2024 Great Britain employment with UK Business 2025 United Kingdom local units; the geography/year mismatch remains visible.",
        "- Survival uses the 2019 birth cohort; current births/deaths/stock use 2024; insolvency rates use England and Wales 2025.",
        "- Missing values remain null. No LLM or unsupported synthetic financial value is used.", "",
        "## Uncertainty", "",
        "Observed turnover-size bands supply interpolated P10/P90 bounds when the quantile lies in a closed band. Otherwise published coefficients of variation support a twice-CV sampling interval. Open-ended quantiles and metrics without a defensible uncertainty measure remain null.", "",
        "## Outputs", "",
    ]
    for key, path in OUTPUTS.items():
        lines.append(f"- {key}: `{path.relative_to(ROOT)}`")
    lines += ["", f"Exact command: `{command}`", ""]
    temp = OUTPUTS["report"].with_name(OUTPUTS["report"].name + ".part")
    temp.write_text("\n".join(lines), encoding="utf-8"); os.replace(temp, OUTPUTS["report"])


def outputs_valid() -> bool:
    try:
        return all(OUTPUTS[key].exists() and pq.ParquetFile(OUTPUTS[key]).metadata.num_rows > 0 for key in ("financial", "labour", "risk", "archetype", "crosswalk"))
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(); group.add_argument("--skip-existing", action="store_true"); group.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.skip_existing and outputs_valid() and not args.force:
        print("All prior outputs already exist and are readable; skipped."); return 0
    for path in INPUTS.values():
        if not path.exists(): raise RuntimeError(f"Missing input {path}")
    fin_obs = pq.read_table(INPUTS["financial"]).to_pylist()
    emp_obs = pq.read_table(INPUTS["employment"]).to_pylist()
    risk_obs = pq.read_table(INPUTS["risk"]).to_pylist()
    labels, sections = sic_context()
    fin = build_financial_priors(fin_obs, labels)
    labour = build_labour_priors(emp_obs, fin_obs, labels, sections)
    risk = build_risk_priors(risk_obs, sections, labels)
    priors, mappings, unresolved = map_archetypes(fin, labour, risk)
    atomic_parquet(fin, FINANCIAL_PRIOR_SCHEMA, OUTPUTS["financial"])
    atomic_parquet(labour, LABOUR_PRIOR_SCHEMA, OUTPUTS["labour"])
    atomic_parquet(risk, RISK_PRIOR_SCHEMA, OUTPUTS["risk"])
    atomic_parquet(priors, ARCHETYPE_PRIOR_SCHEMA, OUTPUTS["archetype"])
    atomic_parquet(mappings, PRIOR_CROSSWALK_SCHEMA, OUTPUTS["crosswalk"])
    atomic_csv(unresolved, UNRESOLVED_FIELDS, OUTPUTS["unresolved"])
    atomic_csv(review_sample(priors), REVIEW_FIELDS, OUTPUTS["review"])
    command = " ".join([sys.executable, str(Path(__file__).relative_to(ROOT)), *(argv if argv is not None else sys.argv[1:])])
    write_report(len(fin_obs), len(emp_obs), len(risk_obs), fin, labour, risk, priors, mappings, unresolved, command)
    print(f"Financial priors: {len(fin):,}")
    print(f"Labour priors: {len(labour):,}")
    print(f"Risk priors: {len(risk):,}")
    print(f"Archetype priors: {len(priors):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
