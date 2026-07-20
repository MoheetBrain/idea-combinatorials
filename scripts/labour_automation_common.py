"""Shared schemas and deterministic I/O for the labour automation evidence layer."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/occupation_source_manifest.csv"


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{hashlib.sha256(stable_json(parts).encode()).hexdigest()[:24]}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest() -> dict[str, dict[str, str]]:
    with MANIFEST.open(newline="", encoding="utf-8") as handle:
        rows = {row["source_id"]: row for row in csv.DictReader(handle)}
    for source_id, row in rows.items():
        path = ROOT / row["local_path"]
        if row["validation_status"] != "passed" or not path.exists():
            raise RuntimeError(f"Source {source_id} is unavailable or invalid")
        if sha256(path) != row["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch for {source_id}")
    return rows


def write_parquet(path: Path, rows: Iterable[dict[str, Any]], schema: pa.Schema, sort_by: list[str]) -> int:
    material = list(rows)
    material.sort(key=lambda row: tuple((row.get(key) is None, str(row.get(key) or "")) for key in sort_by))
    table = pa.Table.from_pylist(material, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    pq.write_table(
        table, temp, compression="zstd", compression_level=9, use_dictionary=False,
        write_statistics=True, row_group_size=65536, data_page_version="1.0",
    )
    pq.read_schema(temp)
    os.replace(temp, path)
    return table.num_rows


def json_array(values: Iterable[Any]) -> str:
    return stable_json([value for value in values if value is not None])


ONTOLOGY_SCHEMA = pa.schema([
    pa.field("occupation_record_id", pa.string()), pa.field("source_taxonomy", pa.string()),
    pa.field("source_occupation_code", pa.string()), pa.field("source_occupation_title", pa.string()),
    pa.field("source_description", pa.string()), pa.field("parent_code", pa.string()),
    pa.field("hierarchy_level", pa.string()), pa.field("release_version", pa.string()),
    pa.field("source_id", pa.string()), pa.field("provenance_json", pa.string()),
])

CROSSWALK_SCHEMA = pa.schema([
    pa.field("left_taxonomy", pa.string()), pa.field("left_code", pa.string()),
    pa.field("left_title", pa.string()), pa.field("right_taxonomy", pa.string()),
    pa.field("right_code", pa.string()), pa.field("right_title", pa.string()),
    pa.field("relationship", pa.string()), pa.field("mapping_weight", pa.float64()),
    pa.field("mapping_method", pa.string()), pa.field("mapping_confidence", pa.float64()),
    pa.field("human_validated", pa.bool_()), pa.field("source_id", pa.string()),
    pa.field("requires_manual_review", pa.bool_()), pa.field("provenance_json", pa.string()),
])

INDUSTRY_OBS_SCHEMA = pa.schema([
    pa.field("observation_id", pa.string()), pa.field("industry_code", pa.string()),
    pa.field("industry_code_level", pa.string()), pa.field("industry_title", pa.string()),
    pa.field("occupation_code", pa.string()), pa.field("occupation_title", pa.string()),
    pa.field("occupation_type", pa.string()), pa.field("occupation_employment", pa.float64()),
    pa.field("industry_total_employment", pa.float64()), pa.field("occupation_share", pa.float64()),
    pa.field("projected_employment", pa.float64()), pa.field("source_year", pa.int64()),
    pa.field("geography", pa.string()), pa.field("evidence_grade", pa.string()),
    pa.field("confidence", pa.float64()), pa.field("source_id", pa.string()),
    pa.field("source_member", pa.string()), pa.field("provenance_json", pa.string()),
])

TASK_SCHEMA = pa.schema([
    pa.field("occupation_code", pa.string()), pa.field("occupation_title", pa.string()),
    pa.field("task_id", pa.string()), pa.field("task_statement", pa.string()),
    pa.field("task_type", pa.string()), pa.field("task_importance", pa.float64()),
    pa.field("task_relevance", pa.float64()), pa.field("task_frequency_if_available", pa.string()),
    pa.field("task_weight_proxy", pa.float64()), pa.field("task_weight_proxy_method", pa.string()),
    pa.field("detailed_work_activity_ids", pa.string()), pa.field("work_context_ids", pa.string()),
    pa.field("source_release", pa.string()), pa.field("source_id", pa.string()),
    pa.field("provenance_json", pa.string()),
])

CAPABILITY_EVIDENCE_SCHEMA = pa.schema([
    pa.field("evidence_id", pa.string()), pa.field("occupation_code", pa.string()),
    pa.field("occupation_title", pa.string()), pa.field("task_id", pa.string()),
    pa.field("task_statement", pa.string()), pa.field("human_exposure_label", pa.string()),
    pa.field("model_exposure_label", pa.string()), pa.field("model_exposure_alt_label", pa.string()),
    pa.field("published_alpha", pa.float64()), pa.field("published_beta", pa.float64()),
    pa.field("published_gamma", pa.float64()), pa.field("published_automation_label", pa.string()),
    pa.field("technology_modality", pa.string()), pa.field("rubric_dimensions_json", pa.string()),
    pa.field("unknown_dimensions_json", pa.string()), pa.field("modality_capability_score", pa.float64()),
    pa.field("technical_assistance_potential", pa.float64()),
    pa.field("end_to_end_task_automation_potential", pa.float64()),
    pa.field("workflow_integration_feasibility", pa.float64()),
    pa.field("reliable_unattended_execution", pa.float64()),
    pa.field("headcount_reducibility", pa.float64()), pa.field("evidence_confidence", pa.float64()),
    pa.field("methodology_version", pa.string()), pa.field("evidence_grade", pa.string()), pa.field("source_id", pa.string()),
    pa.field("provenance_json", pa.string()),
])

INDUSTRY_MIX_SCHEMA = pa.schema([
    *INDUSTRY_OBS_SCHEMA,
    pa.field("occupation_share_low", pa.float64()), pa.field("occupation_share_central", pa.float64()),
    pa.field("occupation_share_high", pa.float64()), pa.field("industry_share_sum", pa.float64()),
    pa.field("industry_matrix_complete", pa.bool_()),
])

ARCHETYPE_MIX_SCHEMA = pa.schema([
    pa.field("archetype_id", pa.string()), pa.field("canonical_name", pa.string()),
    pa.field("industry_source", pa.string()), pa.field("industry_code", pa.string()),
    pa.field("industry_mapping_method", pa.string()), pa.field("occupation_code", pa.string()),
    pa.field("occupation_title", pa.string()), pa.field("occupation_share_low", pa.float64()),
    pa.field("occupation_share_central", pa.float64()), pa.field("occupation_share_high", pa.float64()),
    pa.field("uk_soc_codes_json", pa.string()), pa.field("uk_wage_low", pa.float64()),
    pa.field("uk_wage_central", pa.float64()), pa.field("uk_wage_high", pa.float64()),
    pa.field("mapping_confidence", pa.float64()), pa.field("evidence_grade", pa.string()),
    pa.field("requires_manual_review", pa.bool_()), pa.field("provenance_json", pa.string()),
])

OCCUPATION_AUTOMATION_SCHEMA = pa.schema([
    pa.field("occupation_code", pa.string()), pa.field("occupation_title", pa.string()),
    pa.field("task_count", pa.int64()), pa.field("task_weight_proxy_method", pa.string()),
    pa.field("published_llm_exposure", pa.float64()), pa.field("technical_assistance_potential", pa.float64()),
    pa.field("end_to_end_task_automation_potential", pa.float64()),
    pa.field("workflow_integration_feasibility", pa.float64()),
    pa.field("reliable_unattended_execution", pa.float64()), pa.field("headcount_reducibility", pa.float64()),
    pa.field("llm_assistance_index", pa.float64()), pa.field("software_agent_index", pa.float64()),
    pa.field("rules_based_rpa_index", pa.float64()), pa.field("computer_vision_index", pa.float64()),
    pa.field("speech_interface_index", pa.float64()), pa.field("identity_and_access_index", pa.float64()),
    pa.field("scheduling_and_optimisation_index", pa.float64()), pa.field("predictive_maintenance_index", pa.float64()),
    pa.field("fixed_environment_robotics_index", pa.float64()),
    pa.field("unstructured_environment_robotics_index", pa.float64()),
    pa.field("residual_tasks_json", pa.string()), pa.field("minimum_staffing_floor", pa.float64()),
    pa.field("minimum_on_site_presence", pa.string()), pa.field("remote_supervision_possible", pa.string()),
    pa.field("exception_response_requirement", pa.string()), pa.field("evidence_grade", pa.string()),
    pa.field("confidence", pa.float64()), pa.field("requires_manual_review", pa.bool_()),
    pa.field("provenance_json", pa.string()),
])

ARCHETYPE_TASK_SCHEMA = pa.schema([
    pa.field("archetype_id", pa.string()), pa.field("canonical_name", pa.string()),
    pa.field("occupation_code", pa.string()), pa.field("occupation_title", pa.string()),
    pa.field("occupation_share", pa.float64()), pa.field("task_id", pa.string()),
    pa.field("task_statement", pa.string()), pa.field("task_weight_proxy", pa.float64()),
    pa.field("archetype_task_weight_proxy", pa.float64()), pa.field("published_llm_exposure", pa.float64()),
    pa.field("llm_assistance_score", pa.float64()), pa.field("software_agent_score", pa.float64()),
    pa.field("computer_vision_score", pa.float64()), pa.field("robotics_score", pa.float64()),
    pa.field("end_to_end_automation_score", pa.float64()), pa.field("residual_human_reason", pa.string()),
    pa.field("evidence_grade", pa.string()), pa.field("provenance_json", pa.string()),
])

OPPORTUNITY_SCHEMA = pa.schema([
    pa.field("archetype_id", pa.string()), pa.field("canonical_name", pa.string()),
    pa.field("primary_industry_code", pa.string()), pa.field("industry_mapping_method", pa.string()),
    pa.field("industry_mapping_confidence", pa.float64()),
    pa.field("baseline_employees_per_unit_low", pa.float64()),
    pa.field("baseline_employees_per_unit_central", pa.float64()),
    pa.field("baseline_employees_per_unit_high", pa.float64()),
    pa.field("top_occupations_json", pa.string()), pa.field("top_tasks_json", pa.string()),
    pa.field("llm_assistance_index", pa.float64()), pa.field("software_agent_index", pa.float64()),
    pa.field("computer_vision_index", pa.float64()), pa.field("robotics_index", pa.float64()),
    pa.field("end_to_end_task_automation_index", pa.float64()),
    pa.field("headcount_reducibility_index", pa.float64()),
    pa.field("minimum_staffing_floor", pa.float64()), pa.field("minimum_on_site_presence", pa.string()),
    pa.field("remote_supervision_possible", pa.string()), pa.field("residual_tasks_json", pa.string()),
    pa.field("residual_roles_json", pa.string()), pa.field("payroll_saving_fraction_low", pa.float64()),
    pa.field("payroll_saving_fraction_central", pa.float64()), pa.field("payroll_saving_fraction_high", pa.float64()),
    pa.field("payroll_saving_estimate_available", pa.bool_()),
    pa.field("financial_confidence", pa.float64()), pa.field("occupation_confidence", pa.float64()),
    pa.field("task_confidence", pa.float64()), pa.field("automation_confidence", pa.float64()),
    pa.field("overall_confidence", pa.float64()), pa.field("evidence_grade", pa.string()),
    pa.field("requires_manual_review", pa.bool_()), pa.field("uncertainty_notes", pa.string()),
    pa.field("provenance_json", pa.string()),
])
