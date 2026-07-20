#!/usr/bin/env python3
"""Build deterministic task-capability, residual-human, and automation-opportunity evidence."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import yaml

from labour_automation_common import (
    ARCHETYPE_TASK_SCHEMA,
    CAPABILITY_EVIDENCE_SCHEMA,
    OCCUPATION_AUTOMATION_SCHEMA,
    OPPORTUNITY_SCHEMA,
    ROOT,
    stable_id,
    stable_json,
    write_parquet,
)


INPUTS = {
    "tasks": ROOT / "data/interim/occupation_tasks_v0.parquet",
    "published": ROOT / "data/interim/task_capability_evidence_v0.parquet",
    "crosswalks": ROOT / "data/interim/occupation_crosswalks_v0.parquet",
    "mix": ROOT / "data/processed/archetype_occupation_mix_v0.parquet",
    "archetypes": ROOT / "data/processed/archetypes_v0.csv",
    "priors": ROOT / "data/processed/archetype_priors_v0.parquet",
    "rules": ROOT / "config/automation_rubric.yaml",
}
OUTPUTS = {
    "capability": ROOT / "data/interim/task_capability_evidence_v0.parquet",
    "occupation": ROOT / "data/processed/occupation_task_automation_priors_v0.parquet",
    "tasks": ROOT / "data/processed/archetype_task_model_v0.parquet",
    "opportunity": ROOT / "data/processed/archetype_automation_opportunity_v0.parquet",
    "unresolved": ROOT / "data/processed/unresolved_task_evidence_v0.csv",
    "report": ROOT / "reports/labour_automation_model_report.md",
    "review": ROOT / "reports/labour_automation_review_sample.csv",
}
MODALITIES = [
    "llm_assistance", "software_agent_execution", "rules_based_rpa", "computer_vision",
    "speech_interface", "identity_and_access_automation", "scheduling_and_optimisation",
    "predictive_maintenance", "fixed_environment_robotics", "unstructured_environment_robotics",
]
UNRESOLVED_FIELDS = ["occupation_code", "occupation_title", "task_id", "task_statement", "reason", "requires_manual_review"]
REVIEW_FIELDS = [
    "review_stratum", "archetype_id", "canonical_name", "primary_industry_code",
    "industry_mapping_method", "top_occupations_json", "top_tasks_json",
    "llm_assistance_index", "software_agent_index", "computer_vision_index", "robotics_index",
    "end_to_end_task_automation_index", "headcount_reducibility_index",
    "minimum_staffing_floor", "minimum_on_site_presence", "remote_supervision_possible",
    "residual_tasks_json", "residual_roles_json", "payroll_saving_estimate_available",
    "evidence_grade", "overall_confidence", "requires_manual_review", "uncertainty_notes",
]
ANCHORS = {
    "Self-service laundrette": ["self-service laundrette"],
    "Vehicle washing service": ["vehicle washing service"],
    "Self-storage facility": ["self-storage facility"],
    "Bowling alley": ["bowling alley"],
    "Cafe": ["cafe"],
    "Commercial bakery": ["commercial bakery"],
    "Gym": ["gymnasia/fitness suites", "gym"],
    "Dog-grooming salon": ["pet-grooming salon", "dog-grooming salon"],
    "Parcel collection point": ["parcel collection locker", "parcel collection point"],
    "Private karaoke-room venue": ["private karaoke-room venue"],
}


def bounded(value: float | None) -> float | None:
    return None if value is None else max(0.0, min(1.0, value))


def keyword_match(text: str, keywords: list[str]) -> bool:
    return any(keyword.casefold() in text for keyword in keywords)


def classify_dimensions(statement: str, rules: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    dimensions = {name: "unknown" for name in rules["dimensions"]}
    matched = []
    rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    text = statement.casefold()
    for rule_id, rule in rules["task_rules"].items():
        if keyword_match(text, rule["keywords"]):
            matched.append(rule_id)
            for name, value in rule["dimensions"].items():
                if rank[value] > rank[dimensions[name]]:
                    dimensions[name] = value
    return dimensions, matched


def dimension_score(dimensions: dict[str, str], name: str, scale: dict[str, Any]) -> float | None:
    value = dimensions.get(name, "unknown")
    result = scale.get(value)
    return float(result) if result is not None else None


def modality_scores(statement: str, dimensions: dict[str, str], published: float | None,
                    rules: dict[str, Any]) -> dict[str, float | None]:
    scale = rules["score_scale"]
    text = statement.casefold()
    physical = dimension_score(dimensions, "physical_manipulation_requirement", scale)
    safety = dimension_score(dimensions, "safety_criticality", scale)
    legal = dimension_score(dimensions, "legal_accountability_requirement", scale)
    digital_in = dimension_score(dimensions, "digital_input_availability", scale)
    digital_out = dimension_score(dimensions, "digital_output_acceptability", scale)
    language = dimension_score(dimensions, "language_reasoning_requirement", scale)
    structured = dimension_score(dimensions, "structured_rule_content", scale)
    exception = dimension_score(dimensions, "exception_variability", scale)
    environmental = dimension_score(dimensions, "environmental_variability", scale)
    verification = dimension_score(dimensions, "verification_observability", scale)
    presence = dimension_score(dimensions, "human_presence_requirement", scale)
    classified = any(value != "unknown" for value in dimensions.values())

    llm = published
    if llm is None and all(value is not None for value in (digital_in, digital_out, language)):
        llm = (digital_in + digital_out + language) / 3
    if llm is not None and ((physical or 0) >= 0.85 or (safety or 0) >= 0.85):
        llm = min(llm, 0.25)  # assistance only; never direct physical execution

    software = None
    if (physical or 0) >= 0.85 or (safety or 0) >= 0.85 or (legal or 0) >= 0.85:
        software = 0.0
    elif structured is not None and digital_in is not None and digital_out is not None:
        software = 0.78 if (exception or 0) < 0.85 else 0.35
    elif digital_in is not None and digital_out is not None:
        software = 0.45

    rpa = None
    if (physical or 0) >= 0.85:
        rpa = 0.0
    elif structured is not None and digital_in is not None:
        rpa = 0.75 if (exception or 0) < 0.85 else 0.25

    modality_rules = rules["modality_rules"]
    vision_match = keyword_match(text, rules["task_rules"]["vision"]["keywords"])
    fixed_match = keyword_match(text, modality_rules["fixed_environment_robotics"]["positive_keywords"])
    unstructured_match = keyword_match(text, modality_rules["unstructured_environment_robotics"]["positive_keywords"])
    scores: dict[str, float | None] = {
        "llm_assistance": bounded(llm), "software_agent_execution": bounded(software),
        "rules_based_rpa": bounded(rpa),
        "computer_vision": 0.65 if vision_match else (0.0 if classified else None),
        "speech_interface": 0.70 if keyword_match(text, modality_rules["speech_interface"]["positive_keywords"]) else (0.0 if classified else None),
        "identity_and_access_automation": 0.75 if keyword_match(text, modality_rules["identity_and_access_automation"]["positive_keywords"]) else (0.0 if classified else None),
        "scheduling_and_optimisation": 0.78 if keyword_match(text, modality_rules["scheduling_and_optimisation"]["positive_keywords"]) else (0.0 if classified else None),
        "predictive_maintenance": 0.62 if keyword_match(text, modality_rules["predictive_maintenance"]["positive_keywords"]) else (0.0 if classified else None),
        "fixed_environment_robotics": 0.50 if fixed_match and (environmental or 0) < 0.85 else (0.0 if classified and not fixed_match else None),
        "unstructured_environment_robotics": 0.25 if unstructured_match else (0.0 if classified else None),
    }
    if (safety or 0) >= 0.85:
        for name in ("computer_vision", "fixed_environment_robotics", "unstructured_environment_robotics"):
            if scores[name] is not None:
                scores[name] = min(scores[name], 0.20)
    return scores


def residual_reasons(statement: str, rules: dict[str, Any]) -> list[str]:
    text = statement.casefold()
    return [name for name, keywords in rules["residual_human_categories"].items() if keyword_match(text, keywords)]


def derived_transitions(scores: dict[str, float | None], dimensions: dict[str, str]) -> tuple[float | None, float | None, float | None, float | None]:
    candidates = [scores[name] for name in (
        "software_agent_execution", "rules_based_rpa", "identity_and_access_automation",
        "scheduling_and_optimisation", "fixed_environment_robotics", "unstructured_environment_robotics",
    ) if scores[name] is not None]
    end_to_end = max(candidates) if candidates else None
    if dimensions["physical_manipulation_requirement"] == "high" and not any(
        scores[name] is not None for name in ("fixed_environment_robotics", "unstructured_environment_robotics")
    ):
        end_to_end = 0.0
    if dimensions["safety_criticality"] == "high" or dimensions["legal_accountability_requirement"] == "high":
        end_to_end = min(end_to_end, 0.20) if end_to_end is not None else 0.0
    if dimensions["structured_rule_content"] == "high" and dimensions["exception_variability"] != "high":
        workflow = 0.75
    elif dimensions["digital_input_availability"] == "high":
        workflow = 0.45
    elif dimensions["physical_manipulation_requirement"] == "high" or dimensions["environmental_variability"] == "high":
        workflow = 0.20
    else:
        workflow = None
    reliable = end_to_end * workflow if end_to_end is not None and workflow is not None else None
    if reliable is not None and dimensions["safety_criticality"] == "high":
        reliable = min(reliable, 0.10)
    headcount = reliable
    if dimensions["human_presence_requirement"] == "high" or dimensions["legal_accountability_requirement"] == "high":
        headcount = 0.0
    return bounded(end_to_end), bounded(workflow), bounded(reliable), bounded(headcount)


def weighted_index(tasks: list[dict[str, Any]], field: str) -> tuple[float | None, float]:
    total = sum(row["combined_weight"] for row in tasks)
    known = sum(row["combined_weight"] for row in tasks if row.get(field) is not None)
    if total <= 0 or known / total < 0.40:
        return None, known / total if total else 0.0
    value = sum(row["combined_weight"] * row[field] for row in tasks if row.get(field) is not None) / known
    return bounded(value), known / total


def build_task_evidence(tasks: list[dict[str, Any]], published_rows: list[dict[str, Any]], rules: dict[str, Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    published = {(row["occupation_code"], row["task_id"]): row for row in published_rows}
    scored: dict[tuple[str, str], dict[str, Any]] = {}
    evidence_rows = []
    unresolved = []
    for task in tasks:
        key = (task["occupation_code"], task["task_id"])
        source = published.get(key)
        exposure = source.get("published_beta") if source else None
        dimensions, matched_rules = classify_dimensions(task["task_statement"], rules)
        scores = modality_scores(task["task_statement"], dimensions, exposure, rules)
        end_to_end, workflow, reliable, headcount = derived_transitions(scores, dimensions)
        reasons = residual_reasons(task["task_statement"], rules)
        scored[key] = {
            **task, "published_llm_exposure": exposure, "dimensions": dimensions,
            "matched_rules": matched_rules, "scores": scores, "end_to_end": end_to_end,
            "workflow": workflow, "reliable": reliable, "headcount": headcount, "residual_reasons": reasons,
        }
        unknown = sorted(name for name, value in dimensions.items() if value == "unknown")
        for modality in MODALITIES:
            evidence_rows.append({
                "evidence_id": stable_id("taskcap", task["occupation_code"], task["task_id"], modality),
                "occupation_code": task["occupation_code"], "occupation_title": task["occupation_title"],
                "task_id": task["task_id"], "task_statement": task["task_statement"],
                "human_exposure_label": source.get("human_exposure_label") if source else None,
                "model_exposure_label": source.get("model_exposure_label") if source else None,
                "model_exposure_alt_label": source.get("model_exposure_alt_label") if source else None,
                "published_alpha": source.get("published_alpha") if source else None,
                "published_beta": source.get("published_beta") if source else None,
                "published_gamma": source.get("published_gamma") if source else None,
                "published_automation_label": source.get("published_automation_label") if source else None,
                "technology_modality": modality, "rubric_dimensions_json": stable_json(dimensions),
                "unknown_dimensions_json": stable_json(unknown), "modality_capability_score": scores[modality],
                "technical_assistance_potential": scores["llm_assistance"] if modality == "llm_assistance" else scores[modality],
                "end_to_end_task_automation_potential": end_to_end, "workflow_integration_feasibility": workflow,
                "reliable_unattended_execution": reliable, "headcount_reducibility": headcount,
                "evidence_confidence": 0.72 if modality == "llm_assistance" and source else (0.50 if scores[modality] is not None else 0.0),
                "methodology_version": "automation_rubric_v1",
                "evidence_grade": "D" if modality == "llm_assistance" and source else "E",
                "source_id": "openai_gpts_are_gpts" if modality == "llm_assistance" and source else "automation_rubric_v1",
                "provenance_json": stable_json({
                    "matched_rule_ids": matched_rules, "task_weight_is_not_time_share": True,
                    "published_exposure_is_not_automation_or_job_displacement": True,
                    "unknown_dimensions_preserved": unknown,
                }),
            })
        if source is None or all(value is None for value in scores.values()):
            unresolved.append({
                "occupation_code": task["occupation_code"], "occupation_title": task["occupation_title"],
                "task_id": task["task_id"], "task_statement": task["task_statement"],
                "reason": "No published exposure row" if source is None else "No modality rule produced a capability score",
                "requires_manual_review": "true",
            })
    return scored, evidence_rows, unresolved


def build_occupation_priors(scored: dict[tuple[str, str], dict[str, Any]], crosswalks: list[dict[str, Any]],
                            mix_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    tasks_by_onet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (onet, _), task in scored.items():
        tasks_by_onet[onet].append(task)
    onet_by_us: dict[str, set[str]] = defaultdict(set)
    for row in crosswalks:
        if row["left_taxonomy"] == "ONET_SOC_2019" and row["right_taxonomy"] == "US_SOC_2018":
            onet_by_us[row["right_code"]].add(row["left_code"])
    occupation_titles = {row["occupation_code"]: row["occupation_title"] for row in mix_rows}
    priors: dict[str, dict[str, Any]] = {}
    combined_by_occ: dict[str, list[dict[str, Any]]] = {}
    unresolved = []
    for occupation_code in sorted(occupation_titles):
        onet_codes = sorted(onet_by_us.get(occupation_code, set()))
        if not onet_codes:
            unresolved.append({
                "occupation_code": occupation_code, "occupation_title": occupation_titles[occupation_code],
                "task_id": "", "task_statement": "", "reason": "No official O*NET-SOC mapping for BLS occupation",
                "requires_manual_review": "true",
            })
            continue
        contributions: dict[str, dict[str, Any]] = {}
        usable_onet = [code for code in onet_codes if tasks_by_onet.get(code)]
        for onet in usable_onet:
            bundle = tasks_by_onet[onet]
            total = sum(task["task_weight_proxy"] or 0.0 for task in bundle)
            if total <= 0:
                continue
            for task in bundle:
                weight = (task["task_weight_proxy"] or 0.0) / total / len(usable_onet)
                item = contributions.setdefault(task["task_id"], {**task, "combined_weight": 0.0, "source_onet_codes": []})
                item["combined_weight"] += weight
                item["source_onet_codes"].append(onet)
                item.update({
                    "llm": task["scores"]["llm_assistance"], "software": task["scores"]["software_agent_execution"],
                    "rpa": task["scores"]["rules_based_rpa"], "vision": task["scores"]["computer_vision"],
                    "speech": task["scores"]["speech_interface"], "identity": task["scores"]["identity_and_access_automation"],
                    "scheduling": task["scores"]["scheduling_and_optimisation"],
                    "predictive": task["scores"]["predictive_maintenance"],
                    "fixed_robotics": task["scores"]["fixed_environment_robotics"],
                    "unstructured_robotics": task["scores"]["unstructured_environment_robotics"],
                    "end_to_end": task["end_to_end"], "workflow": task["workflow"],
                    "reliable": task["reliable"], "headcount": task["headcount"],
                })
        bundle = list(contributions.values())
        if not bundle:
            continue
        combined_by_occ[occupation_code] = bundle
        indexes = {}
        coverages = {}
        for field in ("llm", "software", "rpa", "vision", "speech", "identity", "scheduling", "predictive", "fixed_robotics", "unstructured_robotics", "end_to_end", "workflow", "reliable", "headcount"):
            indexes[field], coverages[field] = weighted_index(bundle, field)
        residual = sorted(
            ({"task_id": task["task_id"], "task": task["task_statement"], "reasons": task["residual_reasons"], "weight_proxy": round(task["combined_weight"], 8)}
             for task in bundle if task["residual_reasons"]),
            key=lambda item: (-item["weight_proxy"], item["task_id"]),
        )[:15]
        residual_weight = sum(task["combined_weight"] for task in bundle if task["residual_reasons"])
        title = occupation_titles[occupation_code]
        title_lower = title.casefold()
        on_site = "required" if residual_weight >= 0.25 or any(word in title_lower for word in ("cook", "cleaner", "mechanic", "driver", "attendant", "nurse", "childcare", "guard", "repairer")) else "not_established"
        remote = "conditional" if any(word in title_lower for word in ("reception", "customer service", "bookkeep", "dispatcher", "reservation")) else ("no" if on_site == "required" else "unknown")
        headcount = indexes["headcount"]
        if on_site == "required" and remote == "no" and headcount is not None:
            headcount = 0.0
        confidence = min(0.78, 0.45 + 0.33 * coverages.get("end_to_end", 0.0))
        priors[occupation_code] = {
            "occupation_code": occupation_code, "occupation_title": title, "task_count": len(bundle),
            "task_weight_proxy_method": "O*NET importance×relevance proxy; normalized within O*NET occupation; equal split only across official one-to-many O*NET suboccupations; never interpreted as hours",
            "published_llm_exposure": indexes["llm"], "technical_assistance_potential": indexes["llm"],
            "end_to_end_task_automation_potential": indexes["end_to_end"],
            "workflow_integration_feasibility": indexes["workflow"],
            "reliable_unattended_execution": indexes["reliable"], "headcount_reducibility": headcount,
            "llm_assistance_index": indexes["llm"], "software_agent_index": indexes["software"],
            "rules_based_rpa_index": indexes["rpa"], "computer_vision_index": indexes["vision"],
            "speech_interface_index": indexes["speech"], "identity_and_access_index": indexes["identity"],
            "scheduling_and_optimisation_index": indexes["scheduling"],
            "predictive_maintenance_index": indexes["predictive"],
            "fixed_environment_robotics_index": indexes["fixed_robotics"],
            "unstructured_environment_robotics_index": indexes["unstructured_robotics"],
            "residual_tasks_json": stable_json(residual), "minimum_staffing_floor": None,
            "minimum_on_site_presence": on_site, "remote_supervision_possible": remote,
            "exception_response_requirement": "required" if residual else "unknown",
            "evidence_grade": "D" if all(task["published_llm_exposure"] is not None for task in bundle) else "E",
            "confidence": confidence, "requires_manual_review": len(onet_codes) > 1 or coverages.get("end_to_end", 0) < 0.7,
            "provenance_json": stable_json({
                "source_onet_codes": onet_codes, "one_to_many_preserved": len(onet_codes) > 1,
                "task_score_known_weight_coverage": coverages,
                "minimum_staffing_floor": "null: no establishment-specific safe staffing evidence",
                "headcount_index_is_not_payroll_saving": True,
            }),
        }
    return priors, list(priors.values()), unresolved, combined_by_occ


def archetype_constraints(name: str, rules: dict[str, Any], employee_central: float | None,
                          base_headcount: float | None) -> tuple[float | None, str, str, float | None, str]:
    text = name.casefold()
    constraint = rules["staffing_constraints"]
    regulated = keyword_match(text, constraint["regulated_presence_keywords"])
    on_site_keyword = keyword_match(text, constraint["on_site_required_keywords"])
    remote_keyword = keyword_match(text, constraint["remote_supervision_candidate_keywords"])
    if regulated or on_site_keyword:
        on_site = "required"
    elif remote_keyword:
        on_site = "periodic_or_exception_only"
    else:
        on_site = "not_established"
    if regulated:
        remote = "no"
    elif remote_keyword:
        remote = "conditional"
    else:
        remote = "unknown"
    result = base_headcount
    reason = "No site-specific staffing floor; index reflects only task evidence."
    if result is not None and on_site == "required" and remote == "no":
        if employee_central is not None and employee_central <= 1.5:
            result = 0.0
            reason = "Single-worker prior plus required on-site presence prevents a defensible whole-position reduction."
        else:
            result = min(result, 0.25)
            reason = "Required on-site presence caps reducibility; only consolidation beyond the staffing floor may be possible."
    elif result is not None and remote == "conditional":
        result *= 0.60
        reason = "Remote supervision may consolidate coverage, but reliability and exception response remain unresolved."
    return None, on_site, remote, bounded(result), reason


def aggregate_archetypes(archetypes: list[dict[str, str]], mix_rows: list[dict[str, Any]],
                         occupation_priors: dict[str, dict[str, Any]], combined_by_occ: dict[str, list[dict[str, Any]]],
                         financial_priors: dict[str, dict[str, Any]], rules: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mix_by_arch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in mix_rows:
        mix_by_arch[row["archetype_id"]].append(row)
    opportunities = []
    explain_rows = []
    for archetype in archetypes:
        archetype_id, name = archetype["archetype_id"], archetype["canonical_name"]
        mixes = mix_by_arch.get(archetype_id, [])
        prior = financial_priors.get(archetype_id, {})
        employees = prior.get("employees_per_unit_central")
        known = [(row, occupation_priors.get(row["occupation_code"])) for row in mixes]
        known = [(row, occ) for row, occ in known if occ]
        total_share = sum(row["occupation_share_central"] for row, _ in known)

        def aggregate(field: str) -> float | None:
            pairs = [(row["occupation_share_central"], occ[field]) for row, occ in known if occ.get(field) is not None]
            coverage = sum(share for share, _ in pairs)
            return bounded(sum(share * value for share, value in pairs)) if coverage >= 0.60 else None

        llm = aggregate("llm_assistance_index")
        software = aggregate("software_agent_index")
        vision = aggregate("computer_vision_index")
        fixed_robot = aggregate("fixed_environment_robotics_index")
        unstructured_robot = aggregate("unstructured_environment_robotics_index")
        robotics_values = [value for value in (fixed_robot, unstructured_robot) if value is not None]
        robotics = max(robotics_values) if robotics_values else None
        end_to_end = aggregate("end_to_end_task_automation_potential")
        raw_headcount = aggregate("headcount_reducibility")
        floor, on_site, remote, headcount, constraint_reason = archetype_constraints(name, rules, employees, raw_headcount)
        top_occupations = sorted(mixes, key=lambda row: (-row["occupation_share_central"], row["occupation_code"]))[:10]
        top_occ_json = [{
            "code": row["occupation_code"], "title": row["occupation_title"],
            "share": round(row["occupation_share_central"], 6),
        } for row in top_occupations]
        task_candidates = []
        for occupation in top_occupations:
            bundle = combined_by_occ.get(occupation["occupation_code"], [])
            for task in sorted(bundle, key=lambda item: (-item["combined_weight"], item["task_id"]))[:6]:
                contribution = occupation["occupation_share_central"] * task["combined_weight"]
                task_candidates.append((contribution, occupation, task))
                explain_rows.append({
                    "archetype_id": archetype_id, "canonical_name": name,
                    "occupation_code": occupation["occupation_code"], "occupation_title": occupation["occupation_title"],
                    "occupation_share": occupation["occupation_share_central"], "task_id": task["task_id"],
                    "task_statement": task["task_statement"], "task_weight_proxy": task["task_weight_proxy"],
                    "archetype_task_weight_proxy": contribution, "published_llm_exposure": task["published_llm_exposure"],
                    "llm_assistance_score": task["scores"]["llm_assistance"],
                    "software_agent_score": task["scores"]["software_agent_execution"],
                    "computer_vision_score": task["scores"]["computer_vision"],
                    "robotics_score": max([value for value in (task["scores"]["fixed_environment_robotics"], task["scores"]["unstructured_environment_robotics"]) if value is not None], default=None),
                    "end_to_end_automation_score": task["end_to_end"],
                    "residual_human_reason": ",".join(task["residual_reasons"]) or None,
                    "evidence_grade": "D" if task["published_llm_exposure"] is not None else "E",
                    "provenance_json": stable_json({
                        "occupation_share_is_us_transfer": True, "task_weight_proxy_is_not_time_share": True,
                        "source_onet_codes": sorted(set(task["source_onet_codes"])),
                    }),
                })
        task_candidates.sort(key=lambda item: (-item[0], item[2]["task_id"]))
        top_tasks = [{
            "task_id": task["task_id"], "task": task["task_statement"],
            "occupation": occupation["occupation_title"], "weight_proxy": round(contribution, 8),
            "assistance": task["scores"]["llm_assistance"], "end_to_end": task["end_to_end"],
            "residual_reasons": task["residual_reasons"],
        } for contribution, occupation, task in task_candidates[:12]]
        residual_tasks = [item for item in top_tasks if item["residual_reasons"]]
        residual_roles = [{
            "occupation_code": row["occupation_code"], "occupation_title": row["occupation_title"],
            "share": round(row["occupation_share_central"], 6),
            "requirement": occupation_priors.get(row["occupation_code"], {}).get("minimum_on_site_presence"),
        } for row in top_occupations if occupation_priors.get(row["occupation_code"], {}).get("residual_tasks_json") not in (None, "[]")]
        mapping_confidence = min((row["mapping_confidence"] for row in mixes), default=0.0)
        task_confidence = min(1.0, total_share)
        automation_confidence = 0.65 * task_confidence if mixes else 0.0
        financial_confidence = prior.get("financial_confidence")
        confidence_values = [value for value in (financial_confidence, mapping_confidence, task_confidence, automation_confidence) if value is not None]
        overall = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
        method = mixes[0]["industry_mapping_method"] if mixes else "unmapped"
        manual = (
            not mixes or method in {"business_family_fallback", "archetype_rule_override"}
            or any(row["requires_manual_review"] for row in mixes)
            or task_confidence < 0.70
            or archetype.get("requires_manual_review", "").casefold() == "true"
        )
        uncertainty = "; ".join(filter(None, [
            "US occupation shares are geographic-transfer priors, not UK staffing observations" if mixes else "No occupation mix",
            "O*NET importance/relevance is a task-weight proxy, not hours",
            "No establishment-specific shift, safe-staffing, reliability, or exception-response evidence",
            constraint_reason,
            "Payroll-saving fractions withheld because position reducibility and shift consolidation are not observed",
        ]))
        opportunities.append({
            "archetype_id": archetype_id, "canonical_name": name,
            "primary_industry_code": mixes[0]["industry_code"] if mixes else None,
            "industry_mapping_method": method, "industry_mapping_confidence": mapping_confidence,
            "baseline_employees_per_unit_low": prior.get("employees_per_unit_low"),
            "baseline_employees_per_unit_central": employees,
            "baseline_employees_per_unit_high": prior.get("employees_per_unit_high"),
            "top_occupations_json": stable_json(top_occ_json), "top_tasks_json": stable_json(top_tasks),
            "llm_assistance_index": llm, "software_agent_index": software,
            "computer_vision_index": vision, "robotics_index": robotics,
            "end_to_end_task_automation_index": end_to_end, "headcount_reducibility_index": headcount,
            "minimum_staffing_floor": floor, "minimum_on_site_presence": on_site,
            "remote_supervision_possible": remote, "residual_tasks_json": stable_json(residual_tasks),
            "residual_roles_json": stable_json(residual_roles), "payroll_saving_fraction_low": None,
            "payroll_saving_fraction_central": None, "payroll_saving_fraction_high": None,
            "payroll_saving_estimate_available": False, "financial_confidence": financial_confidence,
            "occupation_confidence": mapping_confidence, "task_confidence": task_confidence,
            "automation_confidence": automation_confidence, "overall_confidence": overall,
            "evidence_grade": "D" if mixes and method not in {"business_family_fallback", "archetype_rule_override"} else "E",
            "requires_manual_review": manual, "uncertainty_notes": uncertainty,
            "provenance_json": stable_json({
                "occupation_index_aggregation": "sum(US occupation share × occupation index); no silent industry-share renormalisation",
                "known_occupation_share": total_share, "task_model_explanation_rows_are_top-contribution_subset": True,
                "headcount_reducibility_is_not_payroll_saving": True, "payroll_fields_intentionally_null": True,
                "external_llm_api_used": False, "rules_version": rules["version"],
            }),
        })
    return opportunities, explain_rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str], sort_key: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    rows.sort(key=sort_key)
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def build_review(opportunities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {row["canonical_name"].casefold(): row for row in opportunities}
    selected: list[tuple[str, dict[str, Any]]] = []
    used = set()
    for anchor, candidates in ANCHORS.items():
        row = next((by_name[name.casefold()] for name in candidates if name.casefold() in by_name), None)
        if row:
            selected.append((f"mandatory_anchor:{anchor}", row))
            used.add(row["archetype_id"])
    strata = [
        ("high_physical", lambda row: row["robotics_index"] is not None),
        ("high_administrative", lambda row: (row["llm_assistance_index"] or 0) >= 0.45),
        ("regulated", lambda row: row["minimum_on_site_presence"] == "required"),
        ("single_worker", lambda row: row["baseline_employees_per_unit_central"] is not None and row["baseline_employees_per_unit_central"] <= 1.5),
        ("multi_shift_proxy", lambda row: any(word in row["canonical_name"].casefold() for word in ("bar", "hotel", "accommodation", "transport", "gym"))),
        ("exact_mapping", lambda row: row["industry_mapping_method"] == "exact_detailed_naics"),
        ("fallback_mapping", lambda row: row["industry_mapping_method"] == "business_family_fallback"),
        ("low_confidence", lambda row: row["overall_confidence"] < 0.45),
        ("high_confidence", lambda row: row["overall_confidence"] >= 0.55),
    ]
    ordered = sorted(opportunities, key=lambda row: (row["canonical_name"], row["archetype_id"]))
    index = 0
    while len(selected) < 100:
        stratum, predicate = strata[index % len(strata)]
        row = next((item for item in ordered if item["archetype_id"] not in used and predicate(item)), None)
        if row is None:
            row = next(item for item in ordered if item["archetype_id"] not in used)
            stratum = "coverage_fill"
        selected.append((stratum, row))
        used.add(row["archetype_id"])
        index += 1
    return [{"review_stratum": stratum, **{field: row.get(field) for field in REVIEW_FIELDS if field != "review_stratum"}} for stratum, row in selected]


def write_report(opportunities: list[dict[str, Any]], occupation_count: int, evidence_count: int,
                 task_count: int, review: list[dict[str, Any]]) -> None:
    mapped = [row for row in opportunities if row["primary_industry_code"]]
    methods: dict[str, int] = defaultdict(int)
    for row in opportunities:
        methods[row["industry_mapping_method"]] += 1
    lines = [
        "# Labour and Automation Evidence Model Report", "",
        "This model separates published LLM exposure, rule-based modality capability, end-to-end execution, residual human work, and headcount reducibility. It does not calculate payroll savings.", "",
        "## Coverage", "",
        f"- Occupation-task automation priors: **{occupation_count:,}**",
        f"- Task capability evidence rows: **{evidence_count:,}**",
        f"- Archetype task explanation rows: **{task_count:,}**",
        f"- Archetypes with occupation mixes: **{len(mapped):,} / {len(opportunities):,}**",
        f"- Mapping methods: `{dict(sorted(methods.items()))}`",
        f"- Payroll-saving estimates available: **{sum(row['payroll_saving_estimate_available'] for row in opportunities):,}**",
        f"- Manual-review rows: **{sum(row['requires_manual_review'] for row in opportunities):,}**", "",
        "## Method safeguards", "",
        "- O*NET task importance and relevance form a labelled proxy; neither is represented as working-time share.",
        "- BLS staffing patterns are US observations transferred to the UK at evidence grade D.",
        "- Published GPT exposure is evidence of potential task impact, not adoption, unattended execution, job loss, or payroll reduction.",
        "- Unknown rubric dimensions remain `unknown`; numerical staffing floors remain null without establishment evidence.",
        "- All payroll-saving fractions are null because shift consolidation, safe staffing, exception coverage, and whole-position reducibility are not directly observed.", "",
        "## Mandatory anchors", "",
    ]
    for sample in review:
        if not sample["review_stratum"].startswith("mandatory_anchor"):
            continue
        lines += [
            f"### {sample['canonical_name']}", "",
            f"- Industry mapping: `{sample['primary_industry_code']}` via `{sample['industry_mapping_method']}`.",
            f"- Top occupations: `{sample['top_occupations_json']}`",
            f"- Top tasks and task-level evidence: `{sample['top_tasks_json']}`",
            f"- Assistance / end-to-end / headcount indices: `{sample['llm_assistance_index']}` / `{sample['end_to_end_task_automation_index']}` / `{sample['headcount_reducibility_index']}`.",
            f"- Physical-automation signals (vision / robotics): `{sample['computer_vision_index']}` / `{sample['robotics_index']}`.",
            f"- Residual human tasks: `{sample['residual_tasks_json']}`",
            f"- Staffing constraint: floor `{sample['minimum_staffing_floor']}`, on-site `{sample['minimum_on_site_presence']}`, remote supervision `{sample['remote_supervision_possible']}`.",
            f"- Can a whole position be removed? **Not established**; the headcount index is an opportunity signal and payroll savings are deliberately unavailable.",
            f"- Evidence / uncertainty: grade `{sample['evidence_grade']}`; {sample['uncertainty_notes']}", "",
        ]
    lines += ["## Quality gate", "", f"A deterministic stratified sample of **{len(review)}** archetypes was generated at `reports/labour_automation_review_sample.csv`. Kill criteria are evaluated by the validation script, not assumed passed here.", ""]
    temp = OUTPUTS["report"].with_name(OUTPUTS["report"].name + ".part")
    temp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temp, OUTPUTS["report"])


def run(force: bool, skip_existing: bool) -> int:
    derived = [OUTPUTS[key] for key in ("occupation", "tasks", "opportunity", "unresolved", "report", "review")]
    if skip_existing and all(path.exists() for path in derived) and not force:
        print("Task/automation outputs already exist; skipped")
        return 0
    rules = yaml.safe_load(INPUTS["rules"].read_text(encoding="utf-8"))
    tasks = pq.read_table(INPUTS["tasks"]).to_pylist()
    published = pq.read_table(INPUTS["published"]).to_pylist()
    crosswalks = pq.read_table(INPUTS["crosswalks"]).to_pylist()
    mix_rows = pq.read_table(INPUTS["mix"]).to_pylist()
    financial_priors = {row["archetype_id"]: row for row in pq.read_table(INPUTS["priors"]).to_pylist()}
    with INPUTS["archetypes"].open(newline="", encoding="utf-8") as handle:
        archetypes = [row for row in csv.DictReader(handle) if row["archetype_level"] == "actionable"]
    scored, evidence_rows, unresolved = build_task_evidence(tasks, published, rules)
    evidence_count = write_parquet(OUTPUTS["capability"], evidence_rows, CAPABILITY_EVIDENCE_SCHEMA, ["occupation_code", "task_id", "technology_modality"])
    occupation_priors, occupation_rows, occupation_unresolved, combined_by_occ = build_occupation_priors(scored, crosswalks, mix_rows)
    unresolved.extend(occupation_unresolved)
    occupation_count = write_parquet(OUTPUTS["occupation"], occupation_rows, OCCUPATION_AUTOMATION_SCHEMA, ["occupation_code"])
    opportunities, explain_rows = aggregate_archetypes(archetypes, mix_rows, occupation_priors, combined_by_occ, financial_priors, rules)
    task_count = write_parquet(OUTPUTS["tasks"], explain_rows, ARCHETYPE_TASK_SCHEMA, ["archetype_id", "occupation_code", "task_id"])
    opportunity_count = write_parquet(OUTPUTS["opportunity"], opportunities, OPPORTUNITY_SCHEMA, ["archetype_id"])
    write_csv(OUTPUTS["unresolved"], unresolved, UNRESOLVED_FIELDS, lambda row: (row["occupation_code"], row["task_id"], row["reason"]))
    review = build_review(opportunities)
    write_csv(OUTPUTS["review"], review, REVIEW_FIELDS, lambda row: (row["review_stratum"], row["canonical_name"]))
    write_report(opportunities, occupation_count, evidence_count, task_count, review)
    print(f"Capability evidence={evidence_count:,}; occupation priors={occupation_count:,}; archetype tasks={task_count:,}; opportunities={opportunity_count:,}; unresolved={len(unresolved):,}")
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
