#!/usr/bin/env python3
"""Build BLS industry mixes and transfer them cautiously to canonical archetypes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import yaml

from labour_automation_common import (
    ARCHETYPE_MIX_SCHEMA,
    INDUSTRY_MIX_SCHEMA,
    ROOT,
    stable_json,
    write_parquet,
)


INPUTS = {
    "observations": ROOT / "data/interim/industry_occupation_observations_v0.parquet",
    "crosswalks": ROOT / "data/interim/occupation_crosswalks_v0.parquet",
    "archetypes": ROOT / "data/processed/archetypes_v0.csv",
    "priors": ROOT / "data/processed/archetype_priors_v0.parquet",
    "employment": ROOT / "data/interim/industry_employment_observations.parquet",
    "sic": ROOT / "data/interim/sic_hierarchy.parquet",
    "rules": ROOT / "config/automation_rubric.yaml",
}
OUTPUTS = {
    "industry": ROOT / "data/processed/industry_occupation_mix_v0.parquet",
    "archetype": ROOT / "data/processed/archetype_occupation_mix_v0.parquet",
    "unresolved": ROOT / "data/processed/unresolved_occupation_mappings_v0.csv",
}
UNRESOLVED_FIELDS = [
    "archetype_id", "canonical_name", "source_naics_codes", "reason",
    "best_available_mapping", "requires_manual_review",
]


def json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        result = json.loads(value)
        return [str(item) for item in result] if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


def build_industry_mix(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sums: dict[str, float] = defaultdict(float)
    for row in observations:
        sums[row["industry_code"]] += row["occupation_share"]
    rows = []
    for row in observations:
        total = sums[row["industry_code"]]
        rows.append({
            **row, "occupation_share_low": None, "occupation_share_central": row["occupation_share"],
            "occupation_share_high": None, "industry_share_sum": total,
            "industry_matrix_complete": 0.97 <= total <= 1.01,
        })
    return rows


def closest_naics(source_code: str, available: set[str]) -> tuple[str | None, str | None]:
    code = re.sub(r"\D", "", source_code)
    if source_code in available:
        return source_code, "exact_detailed_naics"
    if code in available:
        return code, "exact_detailed_naics"
    if len(code) >= 3:
        candidates = []
        for candidate in available:
            if not re.fullmatch(r"\d{6}", candidate):
                continue
            significant = candidate.rstrip("0")
            if len(significant) >= 2 and code.startswith(significant):
                candidates.append(candidate)
        if candidates:
            candidates.sort(key=lambda item: (-len(item.rstrip("0")), item))
            return candidates[0], "parent_naics"
    return None, None


def build_uk_bridge(crosswalks: list[dict[str, Any]]) -> dict[str, list[str]]:
    onet_by_us: dict[str, set[str]] = defaultdict(set)
    isco_by_onet: dict[str, set[str]] = defaultdict(set)
    uk_by_isco: dict[str, set[str]] = defaultdict(set)
    for row in crosswalks:
        if row["left_taxonomy"] == "ONET_SOC_2019" and row["right_taxonomy"] == "US_SOC_2018":
            onet_by_us[row["right_code"]].add(row["left_code"])
        elif row["left_taxonomy"] == "ESCO_ISCO" and row["right_taxonomy"] == "ONET_SOC_2019":
            isco_by_onet[row["right_code"]].add(row["left_code"].split(".")[0])
        elif row["left_taxonomy"] == "UK_SOC_2020" and row["right_taxonomy"] == "ISCO_08":
            uk_by_isco[row["right_code"]].add(row["left_code"])
    result: dict[str, list[str]] = {}
    for us_code, onet_codes in onet_by_us.items():
        isco_codes = {isco for onet in onet_codes for isco in isco_by_onet.get(onet, set())}
        uk_codes = {uk for isco in isco_codes for uk in uk_by_isco.get(isco, set())}
        result[us_code] = sorted(uk_codes)
    return result


def sic_sections() -> dict[str, str]:
    rows = pq.read_table(INPUTS["sic"]).to_pylist()
    parents = {row["source_code"]: row["parent_code"] for row in rows}
    sections = {row["source_code"] for row in rows if row["hierarchy_level"].casefold() == "section"}
    result = {}
    for code in parents:
        current = code
        seen = set()
        while current and current not in seen:
            if current in sections:
                result[code] = current
                break
            seen.add(current)
            current = parents.get(current)
    return result


def ashe_lookup() -> dict[tuple[str, str], dict[str, Any]]:
    rows = pq.read_table(INPUTS["employment"]).to_pylist()
    lookup = {}
    for row in rows:
        if row["source_id"] != "ashe_2025_table29" or not row["industry_code"] or not row["occupation_code"]:
            continue
        key = (row["industry_code"], row["occupation_code"])
        current = lookup.get(key)
        score = (row["median_hourly_pay_gbp"] is not None) + (row["median_annual_pay_gbp"] is not None)
        if current is None or score > current[0]:
            lookup[key] = (score, row)
    return {key: value[1] for key, value in lookup.items()}


def wage_for(section: str | None, uk_codes: list[str], lookup: dict[tuple[str, str], dict[str, Any]]) -> tuple[Any, Any, Any, str | None]:
    if not section or not uk_codes:
        return None, None, None, None
    for width in (2, 1):
        prefixes = {code[:width] for code in uk_codes if len(code) >= width}
        if len(prefixes) != 1:
            continue
        occupation_group = next(iter(prefixes))
        row = lookup.get((section, occupation_group))
        if row and row["median_annual_pay_gbp"] is not None:
            central = row["median_annual_pay_gbp"]
            cv = row["annual_pay_cv"]
            low = max(0.0, central * (1 - 2 * cv / 100)) if cv is not None else None
            high = central * (1 + 2 * cv / 100) if cv is not None else None
            return low, central, high, occupation_group
    return None, None, None, None


def choose_industry(archetype: dict[str, str], available: set[str], fallbacks: dict[str, str],
                    overrides: dict[str, str] | None = None) -> tuple[str | None, str, float, bool, str]:
    codes = sorted(set(json_list(archetype.get("naics_codes"))))
    matches = []
    for code in codes:
        matched, method = closest_naics(code, available)
        if matched:
            matches.append((0 if method == "exact_detailed_naics" else 1, matched, method, code))
    if matches:
        matches.sort()
        _, matched, method, source = matches[0]
        distinct = {item[1] for item in matches}
        confidence = 0.82 if method == "exact_detailed_naics" else 0.68
        manual = len(distinct) > 1 or archetype.get("requires_manual_review", "").casefold() == "true"
        note = f"Mapped source NAICS {source} to BLS matrix {matched}; US staffing pattern transferred to UK."
        if len(distinct) > 1:
            note += f" Multiple candidate industries {sorted(distinct)}; selected most detailed stable match."
        return matched, method, confidence, manual, note
    override = (overrides or {}).get(archetype.get("canonical_name", "").casefold())
    if override in available:
        return override, "archetype_rule_override", 0.50, True, (
            f"No source NAICS mapping; used auditable operation-specific rule "
            f"{archetype['canonical_name']} → {override}."
        )
    family = archetype.get("parent_archetype_name") or ""
    fallback = fallbacks.get(family)
    if fallback in available:
        return fallback, "business_family_fallback", 0.42, True, f"No source NAICS mapping; used configured business-family fallback {family} → {fallback}."
    return None, "unmapped", 0.0, True, "No defensible exact, parent, or configured business-family occupation mapping."


def write_unresolved(rows: list[dict[str, Any]]) -> None:
    OUTPUTS["unresolved"].parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUTS["unresolved"].with_name(OUTPUTS["unresolved"].name + ".part")
    rows.sort(key=lambda row: (row["canonical_name"], row["archetype_id"]))
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=UNRESOLVED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, OUTPUTS["unresolved"])


def run(force: bool, skip_existing: bool) -> int:
    if skip_existing and all(path.exists() for path in OUTPUTS.values()) and not force:
        print("Industry/archetype occupation outputs already exist; skipped")
        return 0
    for path in INPUTS.values():
        if not path.exists():
            raise RuntimeError(f"Missing required input {path.relative_to(ROOT)}")
    observations = pq.read_table(INPUTS["observations"]).to_pylist()
    industry_mix = build_industry_mix(observations)
    write_parquet(OUTPUTS["industry"], industry_mix, INDUSTRY_MIX_SCHEMA, ["industry_code", "occupation_code"])
    by_industry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in industry_mix:
        by_industry[row["industry_code"]].append(row)
    available = set(by_industry)
    crosswalks = pq.read_table(INPUTS["crosswalks"]).to_pylist()
    uk_bridge = build_uk_bridge(crosswalks)
    sections = sic_sections()
    wages = ashe_lookup()
    priors = {row["archetype_id"]: row for row in pq.read_table(INPUTS["priors"]).to_pylist()}
    rules = yaml.safe_load(INPUTS["rules"].read_text(encoding="utf-8"))
    fallbacks = rules["business_family_naics_fallbacks"]
    overrides = rules.get("archetype_naics_overrides", {})
    with INPUTS["archetypes"].open(newline="", encoding="utf-8") as handle:
        archetypes = [row for row in csv.DictReader(handle) if row["archetype_level"] == "actionable"]
    output_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    mapped = 0
    methods: dict[str, int] = defaultdict(int)
    for archetype in archetypes:
        industry, method, confidence, manual, note = choose_industry(archetype, available, fallbacks, overrides)
        methods[method] += 1
        if industry is None:
            unresolved.append({
                "archetype_id": archetype["archetype_id"], "canonical_name": archetype["canonical_name"],
                "source_naics_codes": archetype.get("naics_codes", "[]"), "reason": note,
                "best_available_mapping": "", "requires_manual_review": "true",
            })
            continue
        mapped += 1
        prior = priors.get(archetype["archetype_id"], {})
        sic_code = prior.get("primary_sic_code")
        section = sections.get(sic_code, sic_code if sic_code and sic_code.isalpha() else None)
        for occupation in by_industry[industry]:
            uk_codes = uk_bridge.get(occupation["occupation_code"], [])
            usable_uk = uk_codes if 0 < len(uk_codes) <= 20 else []
            wage_low, wage_central, wage_high, wage_group = wage_for(section, usable_uk, wages)
            output_rows.append({
                "archetype_id": archetype["archetype_id"], "canonical_name": archetype["canonical_name"],
                "industry_source": "BLS National Employment Matrix 2024-34",
                "industry_code": industry, "industry_mapping_method": method,
                "occupation_code": occupation["occupation_code"], "occupation_title": occupation["occupation_title"],
                "occupation_share_low": None, "occupation_share_central": occupation["occupation_share"],
                "occupation_share_high": None,
                "uk_soc_codes_json": stable_json(usable_uk) if usable_uk else None,
                "uk_wage_low": wage_low, "uk_wage_central": wage_central, "uk_wage_high": wage_high,
                "mapping_confidence": confidence, "evidence_grade": "D",
                "requires_manual_review": manual or not occupation["industry_matrix_complete"] or not usable_uk,
                "provenance_json": stable_json({
                    "industry_mapping_note": note, "occupation_share_source": occupation["observation_id"],
                    "geographic_transfer": "United States occupation share used as a UK prior; not an exact UK staffing structure",
                    "share_not_renormalised": True, "industry_share_sum": occupation["industry_share_sum"],
                    "uk_soc_bridge": "US SOC → O*NET-SOC → ESCO/ISCO → UK SOC; null when too ambiguous",
                    "ashe_source": "ashe_2025_table29" if wage_central is not None else None,
                    "ashe_industry_section": section, "ashe_occupation_group": wage_group,
                    "wage_interval_method": "median ± 2×published CV" if wage_low is not None else None,
                }),
            })
        if method == "business_family_fallback":
            unresolved.append({
                "archetype_id": archetype["archetype_id"], "canonical_name": archetype["canonical_name"],
                "source_naics_codes": archetype.get("naics_codes", "[]"), "reason": note,
                "best_available_mapping": industry, "requires_manual_review": "true",
            })
    count = write_parquet(OUTPUTS["archetype"], output_rows, ARCHETYPE_MIX_SCHEMA, ["archetype_id", "occupation_code"])
    write_unresolved(unresolved)
    print(f"Industry mix rows={len(industry_mix):,}; archetype mix rows={count:,}; mapped archetypes={mapped:,}/{len(archetypes):,}; methods={dict(sorted(methods.items()))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--force", action="store_true")
    group.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()
    return run(args.force, args.skip_existing)


if __name__ == "__main__":
    raise SystemExit(main())
