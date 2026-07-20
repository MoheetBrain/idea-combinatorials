#!/usr/bin/env python3
"""Validate the occupation, task, and automation-opportunity evidence layer."""

from __future__ import annotations

import csv
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from labour_automation_common import ROOT, sha256


FILES = {
    "ontology": ROOT / "data/interim/occupation_ontology_v0.parquet",
    "crosswalks": ROOT / "data/interim/occupation_crosswalks_v0.parquet",
    "industry_observations": ROOT / "data/interim/industry_occupation_observations_v0.parquet",
    "tasks": ROOT / "data/interim/occupation_tasks_v0.parquet",
    "capability": ROOT / "data/interim/task_capability_evidence_v0.parquet",
    "industry_mix": ROOT / "data/processed/industry_occupation_mix_v0.parquet",
    "archetype_mix": ROOT / "data/processed/archetype_occupation_mix_v0.parquet",
    "occupation_automation": ROOT / "data/processed/occupation_task_automation_priors_v0.parquet",
    "archetype_tasks": ROOT / "data/processed/archetype_task_model_v0.parquet",
    "opportunity": ROOT / "data/processed/archetype_automation_opportunity_v0.parquet",
    "archetypes": ROOT / "data/processed/archetypes_v0.csv",
    "manifest": ROOT / "data/occupation_source_manifest.csv",
    "review": ROOT / "reports/labour_automation_review_sample.csv",
}
REPORT = ROOT / "reports/labour_automation_validation_report.md"
MODALITIES = {
    "llm_assistance", "software_agent_execution", "rules_based_rpa", "computer_vision",
    "speech_interface", "identity_and_access_automation", "scheduling_and_optimisation",
    "predictive_maintenance", "fixed_environment_robotics", "unstructured_environment_robotics",
}
SCORE_FIELDS = {
    "capability": [
        "modality_capability_score", "technical_assistance_potential",
        "end_to_end_task_automation_potential", "workflow_integration_feasibility",
        "reliable_unattended_execution", "headcount_reducibility", "evidence_confidence",
    ],
    "occupation_automation": [
        "published_llm_exposure", "technical_assistance_potential",
        "end_to_end_task_automation_potential", "workflow_integration_feasibility",
        "reliable_unattended_execution", "headcount_reducibility", "llm_assistance_index",
        "software_agent_index", "rules_based_rpa_index", "computer_vision_index",
        "speech_interface_index", "identity_and_access_index",
        "scheduling_and_optimisation_index", "predictive_maintenance_index",
        "fixed_environment_robotics_index", "unstructured_environment_robotics_index",
        "confidence",
    ],
    "opportunity": [
        "industry_mapping_confidence", "llm_assistance_index", "software_agent_index",
        "computer_vision_index", "robotics_index", "end_to_end_task_automation_index",
        "headcount_reducibility_index", "financial_confidence", "occupation_confidence",
        "task_confidence", "automation_confidence", "overall_confidence",
    ],
}
ANCHOR_EXPECTATIONS = {
    "self-service laundrette": ["laundry", "counter", "maintenance"],
    "vehicle washing service": ["cleaner", "attendant", "automotive"],
    "self-storage facility": ["property", "maintenance", "rental", "office clerk"],
    "bowling alley": ["amusement", "recreation", "reception"],
    "cafe": ["food", "cook", "waiter"],
    "commercial bakery": ["baker", "food", "packaging"],
    "gymnasia/fitness suites": ["exercise", "fitness", "reception"],
    "pet-grooming salon": ["animal", "reception"],
    "parcel collection locker": ["shipping", "freight", "driver", "inventory"],
    "private karaoke-room venue": ["amusement", "recreation", "reception", "waiter"],
}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_json(value: str | None, expected: type) -> Any:
    if value is None:
        raise ValueError("null JSON field")
    parsed = json.loads(value)
    if not isinstance(parsed, expected):
        raise ValueError(f"expected {expected.__name__}")
    return parsed


def add(checks: list[tuple[str, bool, str]], name: str, ok: bool, detail: str) -> None:
    checks.append((name, bool(ok), detail))


def unique(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bool:
    keys = [tuple(row[field] for field in fields) for row in rows]
    return len(keys) == len(set(keys))


def sorted_by(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bool:
    keys = [tuple((row.get(field) is None, str(row.get(field) or "")) for field in fields) for row in rows]
    return keys == sorted(keys)


def finite_unit_interval(rows: list[dict[str, Any]], fields: list[str]) -> tuple[bool, int]:
    bad = 0
    for row in rows:
        for field in fields:
            value = row.get(field)
            if value is not None and (not math.isfinite(value) or value < 0 or value > 1):
                bad += 1
    return bad == 0, bad


def validate() -> tuple[list[tuple[str, bool, str]], list[tuple[str, bool, str]], dict[str, Any]]:
    missing = [str(path.relative_to(ROOT)) for path in FILES.values() if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required outputs: {missing}")

    rows = {key: pq.read_table(path).to_pylist() for key, path in FILES.items() if path.suffix == ".parquet"}
    archetypes = [row for row in load_csv(FILES["archetypes"]) if row["archetype_level"] == "actionable"]
    manifest = load_csv(FILES["manifest"])
    review = load_csv(FILES["review"])
    checks: list[tuple[str, bool, str]] = []
    kills: list[tuple[str, bool, str]] = []

    add(checks, "Official source validation", bool(manifest) and all(row["validation_status"] == "passed" for row in manifest), f"{len(manifest)} manifest rows")
    add(checks, "Positive output row counts", all(rows[key] for key in rows), str({key: len(value) for key, value in rows.items()}))
    add(checks, "Unique occupation identifiers", unique(rows["ontology"], ("occupation_record_id",)), f"{len(rows['ontology']):,} rows")
    add(checks, "Unique occupation-task links", unique(rows["tasks"], ("occupation_code", "task_id")), f"{len(rows['tasks']):,} links")
    add(checks, "Unique capability evidence IDs", unique(rows["capability"], ("evidence_id",)), f"{len(rows['capability']):,} rows")
    add(checks, "Unique archetype opportunities", unique(rows["opportunity"], ("archetype_id",)), f"{len(rows['opportunity']):,} rows")

    ontology_targets = {(row["source_taxonomy"], row["source_occupation_code"]) for row in rows["ontology"]}
    bad_targets = [row for row in rows["crosswalks"] if (row["left_taxonomy"], row["left_code"]) not in ontology_targets or (row["right_taxonomy"], row["right_code"]) not in ontology_targets]
    add(checks, "Crosswalk target integrity", not bad_targets, f"{len(bad_targets)} missing endpoints")
    right_by_left: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in rows["crosswalks"]:
        right_by_left[(row["left_taxonomy"], row["left_code"], row["right_taxonomy"])].add(row["right_code"])
    one_to_many = sum(len(targets) > 1 for targets in right_by_left.values())
    add(checks, "One-to-many mappings preserved", one_to_many > 0, f"{one_to_many:,} one-to-many source mappings")

    industry_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows["industry_mix"]:
        industry_groups[row["industry_code"]].append(row)
    bad_shares = [row for row in rows["industry_mix"] if not math.isfinite(row["occupation_share_central"]) or row["occupation_share_central"] < 0]
    complete_bad = [code for code, group in industry_groups.items() if group[0]["industry_matrix_complete"] and not 0.97 <= sum(item["occupation_share_central"] for item in group) <= 1.01]
    add(checks, "Industry shares finite and non-negative", not bad_shares, f"{len(bad_shares)} invalid shares")
    add(checks, "Complete industry shares sum approximately to one", not complete_bad, f"{len(complete_bad)} invalid complete groups of {len(industry_groups)}")
    add(checks, "Industry shares not silently renormalised", all(json.loads(row["provenance_json"]).get("occupation_share_recalculated_from_employment") is not False for row in rows["industry_observations"]), "Published counts retained; completeness flag stored")

    for table, fields in SCORE_FIELDS.items():
        ok, bad = finite_unit_interval(rows[table], fields)
        add(checks, f"{table} score ranges", ok, f"{bad} values outside [0,1]")
    add(checks, "Technology modalities remain separate", {row["technology_modality"] for row in rows["capability"]} == MODALITIES, str(Counter(row["technology_modality"] for row in rows["capability"])))
    unknown_preserved = any(parse_json(row["unknown_dimensions_json"], list) for row in rows["capability"])
    add(checks, "Unknown rubric values remain explicit", unknown_preserved, "At least one evidence row preserves named unknown dimensions")

    task_proxy_ok = all("not" in row["task_weight_proxy_method"].casefold() and "time" in row["task_weight_proxy_method"].casefold() for row in rows["tasks"])
    add(checks, "Task importance is not time share", task_proxy_ok, "Every task carries an explicit proxy disclaimer")
    evidence_provenance = all(row.get("provenance_json") and json.loads(row["provenance_json"]) is not None for table in rows.values() for row in table if "provenance_json" in row)
    add(checks, "Derived values retain provenance", evidence_provenance, "All rows with a provenance field contain valid JSON")
    grade_ok = all(row.get("evidence_grade") != "F" for table in rows.values() for row in table if "evidence_grade" in row)
    add(checks, "Grade F prohibited", grade_ok, "No unsupported-synthetic evidence grades")

    actionable_ids = {row["archetype_id"] for row in archetypes}
    opportunity_ids = {row["archetype_id"] for row in rows["opportunity"]}
    crosswalk_arch_ids = {row["archetype_id"] for row in rows["archetype_mix"]}
    add(checks, "All actionable archetypes represented", opportunity_ids == actionable_ids, f"{len(opportunity_ids):,}/{len(actionable_ids):,}")
    add(checks, "Archetype occupation links valid", crosswalk_arch_ids <= actionable_ids, f"{len(crosswalk_arch_ids):,} linked archetypes")
    mapped = [row for row in rows["opportunity"] if row["primary_industry_code"]]
    coverage = len(mapped) / len(archetypes)
    add(checks, "Occupation-mix coverage at least 70%", coverage >= 0.70, f"{coverage:.1%}")
    task_covered = [row for row in mapped if parse_json(row["top_tasks_json"], list)]
    add(checks, "Mapped archetypes receive task bundles", len(task_covered) == len(mapped), f"{len(task_covered):,}/{len(mapped):,}")

    payroll_bad = [row for row in rows["opportunity"] if row["payroll_saving_estimate_available"] or any(row[field] is not None for field in ("payroll_saving_fraction_low", "payroll_saving_fraction_central", "payroll_saving_fraction_high"))]
    add(checks, "No unsupported payroll-saving estimates", not payroll_bad, f"{len(payroll_bad)} rows")
    staffing_bad = [row for row in rows["opportunity"] if row["headcount_reducibility_index"] is not None and (not row["minimum_on_site_presence"] or not row["remote_supervision_possible"] or not row["uncertainty_notes"])]
    add(checks, "Headcount results include staffing constraints", not staffing_bad, f"{len(staffing_bad)} incomplete rows")
    residual_bad = [row for row in mapped if not isinstance(parse_json(row["residual_tasks_json"], list), list) or not isinstance(parse_json(row["residual_roles_json"], list), list)]
    add(checks, "Residual-human outputs explicit", not residual_bad, f"{len(residual_bad)} malformed rows")
    uk_bridge_bad = [row for row in rows["archetype_mix"] if row["uk_soc_codes_json"] and len(parse_json(row["uk_soc_codes_json"], list)) > 20]
    add(checks, "Ambiguous US-to-UK mappings not forced", not uk_bridge_bad, f"{len(uk_bridge_bad)} over-broad mappings")

    add(checks, "Ontology deterministic order", sorted_by(rows["ontology"], ("source_taxonomy", "source_occupation_code")), "source taxonomy/code")
    add(checks, "Industry mix deterministic order", sorted_by(rows["industry_mix"], ("industry_code", "occupation_code")), "industry/occupation code")
    add(checks, "Opportunity deterministic order", sorted_by(rows["opportunity"], ("archetype_id",)), "archetype ID")
    add(checks, "Review sample size and anchors", len(review) == 100 and sum(row["review_stratum"].startswith("mandatory_anchor") for row in review) == 10, f"{len(review)} rows")

    by_name = {row["canonical_name"].casefold(): row for row in rows["opportunity"]}
    implausible = []
    for name, expected in ANCHOR_EXPECTATIONS.items():
        row = by_name.get(name)
        if not row:
            implausible.append(f"{name}:missing")
            continue
        titles = " ".join(item["title"] for item in parse_json(row["top_occupations_json"], list)[:5]).casefold()
        if not any(token in titles for token in expected):
            implausible.append(name)
    kills.append(("Operational plausibility", len(implausible) / len(ANCHOR_EXPECTATIONS) <= 0.20, f"{len(implausible)}/10 anchor mixes failed a conservative role-presence check: {implausible}"))

    physical_llm = []
    for row in rows["capability"]:
        if row["technology_modality"] != "llm_assistance":
            continue
        dimensions = parse_json(row["rubric_dimensions_json"], dict)
        if dimensions["physical_manipulation_requirement"] == "high" and (row["modality_capability_score"] or 0) > 0.25:
            physical_llm.append(row["evidence_id"])
    kills.append(("Physical/manual tasks not labelled LLM-executable", not physical_llm, f"{len(physical_llm)} violations"))
    kills.append(("Exposure not converted to payroll reduction", not payroll_bad, f"{len(payroll_bad)} payroll rows"))
    kills.append(("Minimum staffing and shift constraints present", not staffing_bad, f"{len(staffing_bad)} incomplete headcount rows"))
    kills.append(("Ambiguous occupation bridges not forced", not uk_bridge_bad, f"{len(uk_bridge_bad)} forced mappings"))
    kills.append(("Task importance not represented as hours", task_proxy_ok, "Explicit proxy labelling") )
    explain_bad = [row for row in rows["opportunity"] if row["headcount_reducibility_index"] is not None and not row["residual_tasks_json"]]
    kills.append(("Position reducibility is explainable", not explain_bad, f"{len(explain_bad)} unexplained rows"))
    kills.append(("Residual human responsibilities retained", not residual_bad, f"{len(residual_bad)} malformed rows"))
    attractive_weak = [row for row in rows["opportunity"] if (row["headcount_reducibility_index"] or 0) >= 0.50 and row["industry_mapping_method"] in {"business_family_fallback", "archetype_rule_override"} and not row["requires_manual_review"]]
    kills.append(("Attractive scores not driven by unflagged weak fallbacks", not attractive_weak, f"{len(attractive_weak)} violations"))

    methods = Counter(row["industry_mapping_method"] for row in rows["opportunity"])
    metrics = {
        "sources": len(manifest), "occupations": len(rows["ontology"]),
        "crosswalks": len(rows["crosswalks"]), "industry_observations": len(rows["industry_observations"]),
        "occupation_tasks": len(rows["tasks"]), "capability_rows": len(rows["capability"]),
        "actionable": len(archetypes), "mapped": len(mapped), "coverage": coverage,
        "task_covered": len(task_covered), "methods": dict(sorted(methods.items())),
        "automation_covered": sum(row["end_to_end_task_automation_index"] is not None for row in rows["opportunity"]),
        "payroll_rows": len(payroll_bad), "manual_rows": sum(row["requires_manual_review"] for row in rows["opportunity"]),
        "hashes": {key: sha256(path) for key, path in FILES.items() if path.suffix == ".parquet"},
    }
    return checks, kills, metrics


def write_report(checks: list[tuple[str, bool, str]], kills: list[tuple[str, bool, str]], metrics: dict[str, Any]) -> None:
    passed = all(ok for _, ok, _ in checks) and all(ok for _, ok, _ in kills)
    lines = [
        "# Labour and Automation Validation Report", "",
        f"**Validation status: {'PASS' if passed else 'FAIL'}**", "",
        "## Coverage", "",
        f"- Official source payloads: **{metrics['sources']:,}**",
        f"- Source occupation records: **{metrics['occupations']:,}**",
        f"- Occupation crosswalk mappings: **{metrics['crosswalks']:,}**",
        f"- Industry-occupation observations: **{metrics['industry_observations']:,}**",
        f"- Occupation-task links: **{metrics['occupation_tasks']:,}**",
        f"- Task-capability evidence rows: **{metrics['capability_rows']:,}**",
        f"- Archetypes with occupation mixes: **{metrics['mapped']:,}/{metrics['actionable']:,} ({metrics['coverage']:.1%})**",
        f"- Mapping methods: `{metrics['methods']}`",
        f"- Archetypes with task bundles: **{metrics['task_covered']:,}**",
        f"- Archetypes with an end-to-end opportunity index: **{metrics['automation_covered']:,}**",
        f"- Defensible payroll-saving estimates: **{metrics['payroll_rows']:,}**",
        f"- Manual-review rows: **{metrics['manual_rows']:,}**", "",
        "## Validation checks", "",
        "| Check | Status | Evidence |", "|---|---:|---|",
    ]
    lines.extend(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail.replace('|', '/')} |" for name, ok, detail in checks)
    lines += ["", "## Kill criteria", "", "| Criterion | Status | Evidence |", "|---|---:|---|"]
    lines.extend(f"| {name} | {'CLEAR' if ok else 'TRIGGERED'} | {detail.replace('|', '/')} |" for name, ok, detail in kills)
    lines += [
        "", "## Interpretation safeguards", "",
        "- Published LLM exposure remains a research exposure signal; it is not an adoption, unattended-execution, job-loss, or payroll-saving measure.",
        "- O*NET importance/relevance weights remain labelled proxies and are not working-time shares.",
        "- US BLS occupation shares are explicitly graded geographic transfers to the UK.",
        "- Numerical staffing floors and payroll-saving fractions remain null without establishment-level shift and safe-staffing evidence.",
        "- The review sample is a deterministic quality-control artefact; rows marked manual review are not accepted as site-level staffing models.",
        "", "## Current output hashes", "",
    ]
    lines.extend(f"- `{key}`: `{digest}`" for key, digest in sorted(metrics["hashes"].items()))
    lines += [""]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temp = REPORT.with_name(REPORT.name + ".part")
    temp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temp, REPORT)


def main() -> int:
    checks, kills, metrics = validate()
    write_report(checks, kills, metrics)
    failed_checks = [name for name, ok, _ in checks if not ok]
    triggered = [name for name, ok, _ in kills if not ok]
    print(f"Validation checks: {len(checks) - len(failed_checks)}/{len(checks)} passed")
    print(f"Kill criteria: {len(triggered)} triggered")
    print(f"Report: {REPORT.relative_to(ROOT)}")
    if failed_checks:
        print(f"Failed checks: {failed_checks}")
    if triggered:
        print(f"Triggered: {triggered}")
    return 0 if not failed_checks and not triggered else 1


if __name__ == "__main__":
    raise SystemExit(main())
