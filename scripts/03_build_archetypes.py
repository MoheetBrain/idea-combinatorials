#!/usr/bin/env python3
"""Build a deterministic canonical catalogue of grounded physical businesses."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - startup guidance
    raise SystemExit(
        f"Missing dependency {exc.name!r}. Install requirements.txt before running."
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = REPO_ROOT / "config/archetype_rules.yaml"
INTERIM_DIR = REPO_ROOT / "data/interim"
PROCESSED_DIR = REPO_ROOT / "data/processed"
REPORT_DIR = REPO_ROOT / "reports"
BUILD_VERSION = "1"

INPUT_PATHS = {
    "sic_hierarchy": INTERIM_DIR / "sic_hierarchy.parquet",
    "sic_activity": INTERIM_DIR / "sic_activity_index.parquet",
    "voa_scat": INTERIM_DIR / "voa_scat.parquet",
    "naics_hierarchy": INTERIM_DIR / "naics_hierarchy.parquet",
    "naics_descriptions": INTERIM_DIR / "naics_descriptions.parquet",
    "osm": INTERIM_DIR / "osm_business_categories.parquet",
}
OUTPUT_PATHS = {
    "archetypes": PROCESSED_DIR / "archetypes_v0.csv",
    "crosswalk": PROCESSED_DIR / "taxonomy_crosswalk_v0.parquet",
    "aliases": PROCESSED_DIR / "archetype_aliases_v0.parquet",
    "unresolved": PROCESSED_DIR / "unresolved_categories_v0.csv",
    "excluded": PROCESSED_DIR / "excluded_categories_v0.csv",
    "matches": INTERIM_DIR / "archetype_match_candidates_v0.parquet",
    "report": REPORT_DIR / "archetype_build_report.md",
    "review": REPORT_DIR / "archetype_review_sample.csv",
}

ARCHETYPE_COLUMNS = [
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
EXCLUDED_COLUMNS = [
    "source_record_id",
    "source",
    "source_code",
    "source_label",
    "exclusion_reason",
    "rule_id",
    "confidence",
]
UNRESOLVED_COLUMNS = [
    "unresolved_id",
    "source_record_id",
    "source",
    "source_code",
    "source_label",
    "left_source_record_id",
    "right_source_record_id",
    "decision",
    "reason",
    "confidence",
    "blocking_reasons",
]
REVIEW_COLUMNS = [
    "review_stratum",
    "archetype_id",
    "canonical_name",
    "parent_archetype_name",
    "source_count",
    "evidence_strength",
    "normalisation_confidence",
    "requires_manual_review",
    "osm_observed_count_london",
    "source_names",
    "source_record_ids",
    "review_notes",
]

MATCH_SCHEMA = pa.schema(
    [
        pa.field("left_source_record_id", pa.string()),
        pa.field("right_source_record_id", pa.string()),
        pa.field("lexical_similarity", pa.float64()),
        pa.field("embedding_similarity", pa.float64()),
        pa.field("shared_parent", pa.bool_()),
        pa.field("shared_keywords", pa.string()),
        pa.field("blocking_reasons", pa.string()),
        pa.field("decision", pa.string()),
        pa.field("decision_reason", pa.string()),
    ]
)
CROSSWALK_SCHEMA = pa.schema(
    [
        pa.field("archetype_id", pa.string()),
        pa.field("source", pa.string()),
        pa.field("source_record_id", pa.string()),
        pa.field("source_code", pa.string()),
        pa.field("source_label", pa.string()),
        pa.field("relationship", pa.string()),
        pa.field("mapping_confidence", pa.float64()),
        pa.field("mapping_method", pa.string()),
        pa.field("mapping_reason", pa.string()),
        pa.field("requires_manual_review", pa.bool_()),
    ]
)
ALIAS_SCHEMA = pa.schema(
    [
        pa.field("archetype_id", pa.string()),
        pa.field("alias", pa.string()),
        pa.field("normalised_alias", pa.string()),
        pa.field("source", pa.string()),
        pa.field("source_record_id", pa.string()),
        pa.field("sic_code", pa.string()),
        pa.field("alias_type", pa.string()),
        pa.field("confidence", pa.float64()),
    ]
)

STOPWORDS = {
    "and",
    "for",
    "from",
    "in",
    "of",
    "other",
    "service",
    "services",
    "the",
    "to",
    "with",
    "operation",
    "operations",
    "activities",
    "business",
}
SOURCE_PRIORITY = {
    "voa_scat_2026": 5,
    "openstreetmap_greater_london": 4,
    "naics_2022": 3,
    "uk_sic_2007": 2,
}


@dataclass
class BuildState:
    candidates: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    activity_rows: list[dict[str, Any]]
    source_record_ids: set[str]
    input_record_count: int


class UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def json_array(values: Iterable[Any]) -> str:
    return stable_json(sorted({value for value in values if value not in (None, "")}))


def normalise_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"[_/]+", " ", value)
    value = re.sub(r"[^\w&+' -]+", " ", value, flags=re.UNICODE)
    return " ".join(value.casefold().split())


def compact_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def title_phrase(value: str) -> str:
    words = value.replace("_", " ").split()
    minor = {"and", "or", "of", "for", "in", "with", "to"}
    result = []
    for index, word in enumerate(words):
        if word.upper() in {"TV", "ATM", "IT", "DIY", "EV"}:
            result.append(word.upper())
        elif index and word.casefold() in minor:
            result.append(word.casefold())
        else:
            result.append(word[:1].upper() + word[1:])
    return " ".join(result)


def singularise_phrase(value: str) -> str:
    replacements = {
        "Bakeries": "Bakery",
        "Centres": "Centre",
        "Centers": "Center",
        "Facilities": "Facility",
        "Laundries": "Laundry",
        "Offices": "Office",
        "Restaurants": "Restaurant",
        "Shops": "Shop",
        "Stores": "Store",
        "Warehouses": "Warehouse",
    }
    for plural, singular in replacements.items():
        if value.endswith(plural):
            return value[: -len(plural)] + singular
    return value


def canonical_key(value: str) -> str:
    key = normalise_text(value).replace("-", " ")
    key = re.sub(r"\bcentres?\b", "center", key)
    key = re.sub(r"\bstores?\b", "shop", key)
    key = re.sub(r"\blaundromat\b|\blaunderette\b", "laundrette", key)
    key = re.sub(r"\bservices\b", "service", key)
    key = re.sub(r"\brestaurants\b", "restaurant", key)
    key = re.sub(r"\bbakeries\b", "bakery", key)
    return " ".join(key.split())


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


def stable_row_id(prefix: str, *parts: Any) -> str:
    return prefix + "_" + hashlib.sha256(stable_json(parts).encode("utf-8")).hexdigest()[:20]


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        rules = yaml.safe_load(handle)
    if not isinstance(rules, dict) or not rules.get("version"):
        raise ValueError("rules configuration lacks a version")
    return rules


def read_intermediates() -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    for report in (
        REPO_ROOT / "data/source_manifest.csv",
        REPORT_DIR / "source_download_report.md",
        REPORT_DIR / "taxonomy_parse_report.md",
    ):
        if not report.is_file():
            raise FileNotFoundError(f"required provenance input is missing: {report}")
    tables: dict[str, list[dict[str, Any]]] = {}
    source_ids: set[str] = set()
    for name, path in INPUT_PATHS.items():
        parquet_file = pq.ParquetFile(path)
        if parquet_file.metadata is None or parquet_file.metadata.num_rows <= 0:
            raise ValueError(f"input Parquet has no records: {path}")
        rows = pq.read_table(path).to_pylist()
        identifiers = [row["source_record_id"] for row in rows]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f"input contains duplicate source_record_id values: {path}")
        tables[name] = rows
        source_ids.update(identifiers)
    return tables, source_ids


def matches_patterns(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def phrase_matches(text: str, phrase: str) -> bool:
    """Match a configured word or phrase without accidental substring matches."""
    text_tokens = re.findall(r"[a-z0-9]+", normalise_text(text))
    phrase_tokens = re.findall(r"[a-z0-9]+", normalise_text(phrase))
    if not phrase_tokens:
        return False

    def variants(token: str) -> set[str]:
        forms = {token, token + "s", token + "es"}
        if token.endswith("y") and len(token) > 1:
            forms.add(token[:-1] + "ies")
        return forms

    length = len(phrase_tokens)
    for index in range(len(text_tokens) - length + 1):
        window = text_tokens[index : index + length]
        if window[:-1] == phrase_tokens[:-1] and window[-1] in variants(phrase_tokens[-1]):
            return True
    return False


def exclusion_for_label(label: str, rules: Mapping[str, Any]) -> tuple[str, str, float] | None:
    normal = normalise_text(label)
    if matches_patterns(normal, rules["broad_classification_patterns"]):
        return "Overly broad or residual classification label", "broad_label", 0.99
    if matches_patterns(normal, rules["administrative_patterns"]):
        return "Administrative or non-market organisational category", "administrative", 0.99
    for keyword in rules["infrastructure_keywords"]:
        if keyword in normal:
            return "Infrastructure or non-operational property category", "infrastructure", 0.97
    for keyword in rules["digital_only_keywords"]:
        if keyword in normal:
            return "Primarily digital operation outside the physical-business scope", "digital_only", 0.94
    return None


def osm_exclusion_reason(
    primary_key: str,
    primary_value: str,
    observed_count: int,
    rules: Mapping[str, Any],
) -> tuple[str, str, float] | None:
    value = primary_value.strip()
    normal = normalise_text(value)
    configured = rules["non_business_osm_values"].get(primary_key, [])
    if "*" in configured or normal in {normalise_text(item) for item in configured}:
        return "Non-business, infrastructure, or insufficiently venue-specific OSM tag", "osm_non_business", 0.98
    if not normal or normal in {"other", "unknown", "fixme", "vacant", "yes", "no"}:
        return "Generic or unknown OSM tag value", "osm_generic", 0.99
    if ";" in value or "," in value or "=" in value:
        return "Non-canonical multi-value OSM tag requiring review", "osm_multivalue", 0.90
    if not re.fullmatch(r"[A-Za-z0-9_:' .+&/-]+", value):
        return "Malformed or non-canonical OSM tag value", "osm_malformed", 0.95
    if observed_count <= 0:
        return "OSM tag has no positive observation count", "osm_unobserved", 0.99
    return None


def excluded_row(entry: Mapping[str, Any], reason: tuple[str, str, float]) -> dict[str, Any]:
    return {
        "source_record_id": entry["source_record_id"],
        "source": entry["source"],
        "source_code": entry.get("source_code"),
        "source_label": entry.get("source_label"),
        "exclusion_reason": reason[0],
        "rule_id": reason[1],
        "confidence": reason[2],
    }


def unresolved_source_row(
    entry: Mapping[str, Any], reason: str, rule: str, confidence: float
) -> dict[str, Any]:
    return {
        "unresolved_id": stable_row_id("unresolved", entry["source_record_id"], rule),
        "source_record_id": entry["source_record_id"],
        "source": entry["source"],
        "source_code": entry.get("source_code"),
        "source_label": entry.get("source_label"),
        "left_source_record_id": None,
        "right_source_record_id": None,
        "decision": "INSUFFICIENT_INFORMATION",
        "reason": reason,
        "confidence": confidence,
        "blocking_reasons": json_array([rule]),
    }


def source_entry(row: Mapping[str, Any], relationship: str = "equivalent") -> dict[str, Any]:
    return {
        "source": row["source"],
        "source_record_id": row["source_record_id"],
        "source_code": row.get("source_code"),
        "source_label": row.get("source_label"),
        "relationship": relationship,
    }


def make_candidate(
    *,
    source_kind: str,
    primary_record_id: str,
    source_code: str | None,
    label: str,
    description: str | None,
    entries: list[dict[str, Any]],
    observed_count: int | None,
    physical: bool,
    sic_code: str | None = None,
    osm_key: str | None = None,
    osm_value: str | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": stable_row_id("candidate", source_kind, primary_record_id),
        "source_kind": source_kind,
        "primary_record_id": primary_record_id,
        "source_code": source_code,
        "original_label": label,
        "description": description,
        "source_entries": entries,
        "observed_count": observed_count,
        "physical": physical,
        "sic_code": sic_code,
        "osm_key": osm_key,
        "osm_value": osm_value,
    }


def extract_candidates(
    tables: Mapping[str, list[dict[str, Any]]], rules: Mapping[str, Any]
) -> BuildState:
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    sic_rows = tables["sic_hierarchy"]
    subclass_parents = {
        row["parent_code"]
        for row in sic_rows
        if row["hierarchy_level"] == "Subclass" and row["parent_code"]
    }
    for row in sic_rows:
        entry = source_entry(row)
        level = row["hierarchy_level"]
        if level not in {"Class", "Subclass"}:
            excluded.append(
                excluded_row(entry, ("Broad SIC hierarchy heading", "sic_broad_level", 1.0))
            )
            continue
        if level == "Class" and row["source_code"] in subclass_parents:
            excluded.append(
                excluded_row(
                    entry,
                    ("Parent class represented by more specific subclasses", "sic_parent_class", 1.0),
                )
            )
            continue
        reason = exclusion_for_label(row["source_label"], rules)
        if reason:
            excluded.append(excluded_row(entry, reason))
            continue
        candidates.append(
            make_candidate(
                source_kind="sic",
                primary_record_id=row["source_record_id"],
                source_code=row["source_code"],
                label=row["source_label"],
                description=None,
                entries=[entry],
                observed_count=None,
                physical=True,
                sic_code=row.get("leaf_code") or row["source_code"],
            )
        )

    descriptions = {
        row["source_code"]: row["description"] for row in tables["naics_descriptions"]
    }
    for row in tables["naics_hierarchy"]:
        entry = source_entry(row)
        if row["hierarchy_level"] != "national_industry":
            excluded.append(
                excluded_row(entry, ("Broad NAICS hierarchy heading", "naics_broad_level", 1.0))
            )
            continue
        reason = exclusion_for_label(row["source_label"], rules)
        if reason:
            excluded.append(excluded_row(entry, reason))
            continue
        candidates.append(
            make_candidate(
                source_kind="naics",
                primary_record_id=row["source_record_id"],
                source_code=row["source_code"],
                label=row["source_label"],
                description=descriptions.get(row["source_code"]),
                entries=[entry],
                observed_count=None,
                physical=True,
            )
        )

    voa_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tables["voa_scat"]:
        voa_groups[row["source_code"]].append(row)
    for code, group in sorted(voa_groups.items()):
        primary = next(row for row in group if row["geography"] == "England and Wales")
        entries = [source_entry(row) for row in sorted(group, key=lambda r: r["source_record_id"])]
        reason = exclusion_for_label(primary["source_label"], rules)
        if reason or not primary["physical_venue_candidate"]:
            final_reason = reason or (
                "VOA rule marks category as non-venue infrastructure",
                "voa_non_venue",
                0.96,
            )
            excluded.extend(excluded_row(entry, final_reason) for entry in entries)
            continue
        candidates.append(
            make_candidate(
                source_kind="voa",
                primary_record_id=primary["source_record_id"],
                source_code=code,
                label=primary["source_label"],
                description=None,
                entries=entries,
                observed_count=primary["observed_count"],
                physical=True,
            )
        )

    osm_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in tables["osm"]:
        osm_groups[(row["primary_key"], row["primary_value"])].append(row)
    for (primary_key, primary_value), group in sorted(osm_groups.items()):
        count = sum(int(row["observed_count"] or 0) for row in group)
        entries = [
            source_entry(
                row,
                "equivalent" if row["secondary_tags_json"] == "{}" else "example",
            )
            for row in sorted(group, key=lambda r: r["source_record_id"])
        ]
        reason = osm_exclusion_reason(primary_key, primary_value, count, rules)
        if reason:
            excluded.extend(excluded_row(entry, reason) for entry in entries)
            continue
        if count < 2:
            unresolved.extend(
                unresolved_source_row(
                    entry,
                    "Single OSM observation without corroborating taxonomy evidence",
                    "osm_single_observation",
                    0.55,
                )
                for entry in entries
            )
            continue
        label = f"{primary_key}={primary_value}"
        candidates.append(
            make_candidate(
                source_kind="osm",
                primary_record_id=group[0]["source_record_id"],
                source_code=label,
                label=label,
                description=None,
                entries=entries,
                observed_count=count,
                physical=True,
                osm_key=primary_key,
                osm_value=primary_value,
            )
        )
    source_record_ids = {
        row["source_record_id"] for rows in tables.values() for row in rows
    }
    return BuildState(
        candidates,
        excluded,
        unresolved,
        list(tables["sic_activity"]),
        source_record_ids,
        sum(len(rows) for rows in tables.values()),
    )


OSM_NAME_MAP = {
    ("amenity", "bank"): "Bank branch",
    ("amenity", "car_wash"): "Vehicle washing service",
    ("amenity", "charging_station"): "Vehicle charging station",
    ("amenity", "fast_food"): "Fast-food outlet",
    ("amenity", "fuel"): "Fuel station",
    ("amenity", "ice_cream"): "Ice-cream shop",
    ("amenity", "karaoke_box"): "Private karaoke-room venue",
    ("amenity", "parcel_locker"): "Parcel collection locker",
    ("amenity", "self_storage"): "Self-storage facility",
    ("craft", "electronics_repair"): "Electronics repair service",
    ("leisure", "adult_gaming_centre"): "Adult gaming centre",
    ("leisure", "bowling_alley"): "Bowling alley",
    ("leisure", "escape_game"): "Escape-room venue",
    ("leisure", "fitness_centre"): "Fitness centre",
    ("leisure", "golf_course"): "Golf course",
    ("leisure", "karaoke"): "Karaoke venue",
    ("leisure", "sports_centre"): "Sports centre",
    ("leisure", "swimming_pool"): "Swimming pool",
    ("shop", "beauty"): "Beauty salon",
    ("shop", "bookmaker"): "Betting shop",
    ("shop", "car_repair"): "Vehicle repair workshop",
    ("shop", "convenience"): "Convenience store",
    ("shop", "dry_cleaning"): "Dry-cleaning establishment",
    ("shop", "hairdresser"): "Hairdressing salon",
    ("shop", "laundromat"): "Self-service laundrette",
    ("shop", "laundry"): "Laundry service",
    ("shop", "pet_grooming"): "Pet-grooming salon",
    ("shop", "storage_rental"): "Self-storage facility",
    ("shop", "tool_hire"): "Tool-hire shop",
}


def base_candidate_name(candidate: Mapping[str, Any]) -> str:
    if candidate["source_kind"] == "osm":
        key = (candidate["osm_key"], candidate["osm_value"])
        if key in OSM_NAME_MAP:
            return OSM_NAME_MAP[key]
        value = title_phrase(candidate["osm_value"].replace("_", " "))
        if candidate["osm_key"] == "shop":
            direct = {
                "Bakery",
                "Butcher",
                "Florist",
                "Mall",
                "Newsagent",
                "Optician",
                "Pharmacy",
                "Supermarket",
            }
            return singularise_phrase(value) if value in direct else f"{singularise_phrase(value)} shop"
        if candidate["osm_key"] == "office":
            return value if value.endswith("Agency") else f"{value} office"
        if candidate["osm_key"] == "craft":
            return value if value in {"Bakery", "Brewery", "Caterer"} else f"{value} service"
        if candidate["osm_key"] == "healthcare":
            return value if any(word in value for word in ("Clinic", "Hospital", "Hospice")) else f"{value} practice"
        if candidate["osm_key"] == "tourism":
            return singularise_phrase(value)
        if candidate["osm_key"] == "leisure":
            return value if any(word in value for word in ("Centre", "Course", "Pool", "Stadium")) else f"{value} venue"
        if candidate["osm_key"] == "amenity":
            return singularise_phrase(value)
        return f"{value} operation"

    name = compact_text(candidate["original_label"])
    if candidate["source_kind"] == "naics":
        name = re.sub(r"(?<=[a-z)])T$", "", name).strip()
    transformations = [
        (r"^Manufacture of (.+)$", lambda m: f"{m.group(1)} manufacturing"),
        (r"^Retail sale of (.+?) in specialised stores$", lambda m: f"{m.group(1)} retail shop"),
        (r"^Wholesale of (.+)$", lambda m: f"{m.group(1)} wholesaler"),
        (r"^Repair of (.+)$", lambda m: f"{m.group(1)} repair service"),
        (r"^Renting and leasing of (.+)$", lambda m: f"{m.group(1)} rental service"),
        (r"^Growing of (.+)$", lambda m: f"{m.group(1)} farm"),
        (r"^Raising of (.+)$", lambda m: f"{m.group(1)} farm"),
    ]
    for pattern, replacement in transformations:
        match = re.match(pattern, name, flags=re.IGNORECASE)
        if match:
            name = replacement(match)
            break
    return singularise_phrase(name[:1].upper() + name[1:])


def apply_override(
    name: str, original_label: str, rules: Mapping[str, Any]
) -> tuple[str, str | None, str]:
    searchable_values = [normalise_text(name), normalise_text(original_label)]
    for rule in rules["canonical_overrides"]:
        if any(
            re.search(rule["pattern"], value, flags=re.IGNORECASE)
            for value in searchable_values
        ):
            return rule["canonical_name"], rule.get("family_id"), "canonical_override"
    return name, None, "source_label_cleanup"


def choose_family(
    text: str, rules: Mapping[str, Any], override_family: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    families = {family["id"]: family for family in rules["families"]}
    if override_family:
        family = families[override_family]
    else:
        precedence = {
            family_id: index
            for index, family_id in enumerate(rules["family_precedence"])
        }
        scored: list[tuple[float, int, str, dict[str, Any]]] = []
        for item in rules["families"]:
            matches = [keyword for keyword in item["keywords"] if phrase_matches(text, keyword)]
            if not matches:
                continue
            score = sum(float(len(normalise_text(keyword).split())) for keyword in matches)
            scored.append(
                (-score, precedence.get(item["id"], len(precedence)), item["id"], item)
            )
        family = min(scored)[-1] if scored else families["general_location_business"]
    domains = {domain["id"]: domain for domain in rules["domains"]}
    return family, domains[family["domain_id"]]


def contains_any(text: str, values: Iterable[str]) -> bool:
    return any(phrase_matches(text, value) for value in values)


def derive_mechanics(
    canonical_name: str,
    context: str,
    family: Mapping[str, Any],
    domain: Mapping[str, Any],
    candidate: Mapping[str, Any],
    rules: Mapping[str, Any],
) -> dict[str, Any]:
    text = normalise_text(f"{canonical_name} {context}")
    flags: set[str] = set()
    if contains_any(text, rules["self_service_indicators"]):
        flags.add("self_service")
    if contains_any(text, rules["full_service_indicators"]):
        flags.add("full_service")
    if contains_any(text, rules["mobile_service_keywords"]):
        flags.add("mobile")
    if contains_any(text, rules["rental_indicators"]):
        flags.add("rental")
    if contains_any(text, rules["repair_indicators"]):
        flags.add("repair")
    if contains_any(text, rules["storage_indicators"]):
        flags.add("storage")
    if re.search(r"\b(?:retail\w*|shops?|stores?|dealers?)\b", text) and not flags & {
        "rental",
        "repair",
        "storage",
    }:
        flags.add("retail")
        flags.add("sale")
    if re.search(r"\b(?:manufactur\w*|produc\w*|processing|fabrication)\b", text):
        flags.add("production")
    if re.search(r"\b(?:wholesal\w*|industrial|commercial)\b", text):
        flags.add("commercial")
    if re.search(r"\b(?:consumer|personal|household|salon)\b", text):
        flags.add("consumer")
    if re.search(r"\bindoor\b", text):
        flags.add("indoor")
    if re.search(r"\boutdoor\b", text):
        flags.add("outdoor")
    if re.search(r"\b(?:shops?|stores?|centres?|centers?|facilit(?:y|ies)|factor(?:y|ies)|warehouses?|salons?|restaurants?|hotels?|clinics?|offices?|studios?|venues?)\b", text):
        flags.add("fixed_site")

    if family["id"] == "manufacturing_operations":
        operation_type = "production"
    elif family["id"] == "farming":
        operation_type = "farming"
    elif family["id"] == "extraction_resources":
        operation_type = "extraction"
    elif family["id"] == "vehicle_washing":
        operation_type = "vehicle_washing"
    elif family["id"] == "laundry_services":
        operation_type = "laundry"
    elif "rental" in flags:
        operation_type = "rental"
    elif "repair" in flags:
        operation_type = "repair"
    elif "storage" in flags:
        operation_type = "storage"
    elif "production" in flags:
        operation_type = "production"
    elif "retail" in flags:
        operation_type = "retail"
    elif family["id"] in {
        "restaurants_and_food_service",
        "bars_and_nightlife",
        "accommodation",
    } or contains_any(text, rules["hospitality_indicators"]):
        operation_type = "hospitality"
    elif family["id"] in {"healthcare_facilities", "social_care"} or contains_any(
        text, rules["healthcare_indicators"]
    ):
        operation_type = "healthcare"
    elif family["id"] == "childcare" or contains_any(
        text, rules["childcare_indicators"]
    ):
        operation_type = "childcare"
    elif family["id"] in {"sports_venues", "entertainment_venues"}:
        operation_type = "admission"
    elif family["id"] == "education_training":
        operation_type = "education"
    elif family["id"] in {"logistics_services", "passenger_transport"}:
        operation_type = "transport"
    elif family["id"] == "wholesale_distribution":
        operation_type = "wholesale"
    else:
        operation_type = "service"

    customer_type = (
        "business"
        if flags & {"commercial", "production"} or operation_type in {"wholesale"}
        else "consumer"
        if flags & {"consumer", "retail"} or operation_type in {"hospitality", "admission"}
        else "business_and_consumer"
    )
    subject = canonical_name.casefold()
    templates = {
        "rental": (
            f"Temporary access to {subject}",
            "Maintain, reserve and hand over physical assets for time-limited use",
            "per rental period",
            "Hire inventory, booking system and maintenance equipment",
            "Hire depot, shop or managed collection point",
        ),
        "repair": (
            f"Restoration or maintenance provided by a {subject}",
            "Inspect, repair, service and test customer assets",
            "per repair job or service visit",
            "Workshop tools, diagnostic equipment and parts inventory",
            "Workshop, service unit or customer premises",
        ),
        "storage": (
            f"Secure physical storage through a {subject}",
            "Allocate, secure and provide access to storage space",
            "per storage unit and rental period",
            "Storage units, access control and security systems",
            "Warehouse, depot or purpose-built storage facility",
        ),
        "production": (
            f"Supply of goods produced by a {subject}",
            "Transform physical inputs into the named product",
            "per unit or production batch",
            "Production machinery, work areas and materials handling equipment",
            "Factory, workshop or processing site",
        ),
        "farming": (
            f"Supply of crops, livestock or farm products from a {subject}",
            "Grow, raise, tend and harvest the named agricultural output",
            "per unit, weight or production batch",
            "Agricultural land, husbandry equipment and crop or livestock facilities",
            "Farm, nursery, growing site or managed agricultural land",
        ),
        "extraction": (
            f"Supply of raw materials from a {subject}",
            "Extract, grade and handle the named natural resource",
            "per unit, weight or extraction contract",
            "Extraction plant, heavy equipment and materials-handling systems",
            "Mine, quarry, forestry site, fishery or resource-processing site",
        ),
        "vehicle_washing": (
            f"Cleaning and presentation of vehicles through a {subject}",
            "Receive, wash, rinse, dry and finish customer vehicles",
            "per vehicle or wash package",
            "Wash bay, water-treatment equipment and vehicle-cleaning systems",
            "Dedicated wash site, forecourt bay or mobile operating base",
        ),
        "laundry": (
            f"Cleaning and care of textiles through a {subject}",
            "Receive, wash or dry-clean, dry, finish and return textile items",
            "per load, item or service contract",
            "Washers or dry-cleaning machines, dryers, finishing equipment and counters",
            "Laundrette, dry-cleaning shop or commercial laundry plant",
        ),
        "retail": (
            f"Purchase of goods from a {subject}",
            "Source, display and sell physical goods to customers",
            "per item or customer order",
            "Retail premises, fixtures, stock and point-of-sale equipment",
            "High-street shop, retail park unit or market premises",
        ),
        "hospitality": (
            f"Prepared food, drink or hospitality from a {subject}",
            "Prepare and serve food, drink or accommodation to customers",
            "per order, meal or occupied night",
            "Customer premises, service equipment and hospitality fixtures",
            "Restaurant, hospitality venue or accommodation property",
        ),
        "healthcare": (
            f"Diagnosis, treatment or health support from a {subject}",
            "Assess customers and deliver the named health or care service",
            "per consultation, treatment or care period",
            "Clinical rooms, specialist equipment and records systems",
            "Clinic, practice, care facility or hospital site",
        ),
        "childcare": (
            f"Supervised childcare from a {subject}",
            "Supervise, care for and support children in a controlled setting",
            "per child-place or care session",
            "Safeguarded premises, activity space and childcare equipment",
            "Nursery, children's centre or day-care premises",
        ),
        "education": (
            f"Instruction or training from a {subject}",
            "Deliver structured teaching, practice and assessment",
            "per course, lesson or learner-place",
            "Teaching rooms, instructional equipment and learning materials",
            "School, training centre, studio or specialist teaching site",
        ),
        "admission": (
            f"Participation or entertainment at a {subject}",
            "Operate a physical venue and deliver the named leisure experience",
            "per admission, session or membership period",
            "Venue-specific activity equipment, customer areas and safety systems",
            "Dedicated indoor or outdoor leisure venue",
        ),
        "transport": (
            f"Movement or delivery provided by a {subject}",
            "Collect, transport and deliver passengers or physical goods",
            "per journey, delivery or shipment",
            "Vehicles, handling equipment and dispatch systems",
            "Transport depot, terminal or mobile operating base",
        ),
        "wholesale": (
            f"Bulk procurement from a {subject}",
            "Source, store and distribute goods in commercial quantities",
            "per wholesale order or shipment",
            "Warehouse space, bulk inventory and materials-handling equipment",
            "Warehouse, wholesale market or distribution depot",
        ),
        "service": (
            f"Access to the named service: {subject}",
            f"Perform the physical or location-dependent work described by {subject}",
            "per service transaction or contract",
            "Premises, tools and specialist equipment required by the named operation",
            "Commercial premises, workshop, office or customer site",
        ),
    }
    customer_need, process, revenue_unit, assets, location = templates[operation_type]
    mobile_possible = "mobile" in flags or family["id"] in {
        "repair_services",
        "skilled_trades",
        "construction_services",
        "logistics_services",
    }
    fixed_required = not mobile_possible or "fixed_site" in flags
    return {
        "operation_type": operation_type,
        "customer_need": customer_need,
        "customer_type": customer_type,
        "core_physical_process": process,
        "revenue_unit": revenue_unit,
        "required_assets": assets,
        "typical_location": location,
        "candidate_physicality": "physical_or_location_dependent",
        "physical_venue": bool(candidate["physical"]),
        "fixed_site_required": fixed_required,
        "mobile_operation_possible": mobile_possible,
        "distinction_flags": sorted(flags),
        "family_id": family["id"],
        "family_name": family["name"],
        "domain_id": domain["id"],
        "domain_name": domain["name"],
    }


def enrich_candidates(
    candidates: list[dict[str, Any]], rules: Mapping[str, Any]
) -> list[dict[str, Any]]:
    for candidate in candidates:
        base_name = base_candidate_name(candidate)
        canonical_name, override_family, method = apply_override(
            base_name, candidate["original_label"], rules
        )
        context = candidate["original_label"]
        family, domain = choose_family(
            f"{canonical_name} {candidate['original_label']}", rules, override_family
        )
        family_assignment_method = "official_label"
        if (
            not override_family
            and family["id"] == "general_location_business"
            and candidate.get("description")
        ):
            contextual_family, contextual_domain = choose_family(
                candidate["description"], rules
            )
            if contextual_family["id"] != "general_location_business":
                family, domain = contextual_family, contextual_domain
                family_assignment_method = "naics_description_fallback"
        mechanics = derive_mechanics(
            canonical_name, context, family, domain, candidate, rules
        )
        candidate.update(
            {
                "canonical_name": canonical_name,
                "canonical_key": canonical_key(canonical_name),
                "normalisation_method": method,
                "family_assignment_method": family_assignment_method,
                "important_keywords": important_keywords(canonical_name),
                **mechanics,
            }
        )
    return candidates


def important_keywords(text: str) -> list[str]:
    return sorted(
        {
            token
            for token in re.findall(r"[a-z0-9]+", normalise_text(text))
            if len(token) >= 4 and token not in STOPWORDS
        }
    )


def mechanics_conflict(left: Mapping[str, Any], right: Mapping[str, Any], rules: Mapping[str, Any]) -> list[str]:
    left_flags = set(left.get("distinction_flags", []))
    right_flags = set(right.get("distinction_flags", []))
    conflicts = []
    for first, second in rules["operating_model_distinctions"]:
        if (
            first in left_flags
            and second in right_flags
            and second not in left_flags
            and first not in right_flags
        ) or (
            second in left_flags
            and first in right_flags
            and first not in left_flags
            and second not in right_flags
        ):
            conflicts.append(f"{first}_vs_{second}")
    return sorted(conflicts)


def lexical_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    left_key = left["canonical_key"]
    right_key = right["canonical_key"]
    ratio = SequenceMatcher(None, left_key, right_key).ratio()
    left_words = set(left["important_keywords"])
    right_words = set(right["important_keywords"])
    union = left_words | right_words
    jaccard = len(left_words & right_words) / len(union) if union else 0.0
    return round(max(ratio, jaccard), 6)


def adjudicate_pair(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    similarity: float,
    rules: Mapping[str, Any],
) -> tuple[str, str]:
    conflicts = mechanics_conflict(left, right, rules)
    same_family = left["family_id"] == right["family_id"]
    if left["canonical_key"] == right["canonical_key"]:
        if conflicts:
            return "RELATED_BUT_DISTINCT", "Exact cleaned label but operating-model conflict: " + ", ".join(conflicts)
        return "SAME_ARCHETYPE", "Exact cleaned canonical label with compatible operating mechanics"
    if conflicts and same_family and similarity >= 0.45:
        return "RELATED_BUT_DISTINCT", "Shared business family but economically distinct mechanics: " + ", ".join(conflicts)
    left_words = set(left["important_keywords"])
    right_words = set(right["important_keywords"])
    if same_family and similarity >= 0.72 and (left_words < right_words or right_words < left_words):
        return "PARENT_CHILD", "One label is materially broader within the same business family"
    if same_family and similarity >= 0.82:
        return "INSUFFICIENT_INFORMATION", "High lexical similarity without deterministic equivalence evidence"
    return "RELATED_BUT_DISTINCT", "Related vocabulary does not establish equivalent operating mechanics"


def build_match_candidates(
    candidates: list[dict[str, Any]], rules: Mapping[str, Any]
) -> list[dict[str, Any]]:
    by_exact: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_exact[(candidate["canonical_key"], candidate["family_id"])].append(candidate)
        for token in candidate["important_keywords"]:
            by_token[token].append(candidate)
    pair_reasons: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    for group in by_exact.values():
        for left, right in combinations(sorted(group, key=lambda c: c["candidate_id"]), 2):
            pair_reasons[tuple(sorted((left["candidate_id"], right["candidate_id"])))].add(
                "exact_cleaned_label"
            )
    for token, group in by_token.items():
        if len(group) > 45:
            continue
        for left, right in combinations(sorted(group, key=lambda c: c["candidate_id"]), 2):
            if left["source_kind"] == right["source_kind"]:
                continue
            pair_reasons[tuple(sorted((left["candidate_id"], right["candidate_id"])))].add(
                f"shared_keyword:{token}"
            )
    rows: list[dict[str, Any]] = []
    for pair, reasons in sorted(pair_reasons.items()):
        left = candidate_by_id[pair[0]]
        right = candidate_by_id[pair[1]]
        similarity = lexical_similarity(left, right)
        exact = "exact_cleaned_label" in reasons
        if not exact and similarity < 0.64:
            continue
        decision, decision_reason = adjudicate_pair(left, right, similarity, rules)
        rows.append(
            {
                "left_source_record_id": left["primary_record_id"],
                "right_source_record_id": right["primary_record_id"],
                "lexical_similarity": similarity,
                "embedding_similarity": None,
                "shared_parent": left["family_id"] == right["family_id"],
                "shared_keywords": json_array(
                    set(left["important_keywords"]) & set(right["important_keywords"])
                ),
                "blocking_reasons": json_array(reasons),
                "decision": decision,
                "decision_reason": decision_reason,
                "left_candidate_id": left["candidate_id"],
                "right_candidate_id": right["candidate_id"],
            }
        )
    return rows


def group_candidates(
    candidates: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    union = UnionFind(candidate["candidate_id"] for candidate in candidates)
    for row in matches:
        if row["decision"] == "SAME_ARCHETYPE":
            union.union(row["left_candidate_id"], row["right_candidate_id"])
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[union.find(candidate["candidate_id"])].append(candidate)
    return [sorted(group, key=lambda item: item["candidate_id"]) for _, group in sorted(groups.items())]


def candidate_confidence(candidate: Mapping[str, Any]) -> float:
    if candidate["source_kind"] == "voa":
        return 0.94
    if candidate["source_kind"] == "sic":
        return 0.91
    if candidate["source_kind"] == "naics":
        return 0.92
    count = int(candidate.get("observed_count") or 0)
    return 0.90 if count >= 100 else 0.82 if count >= 10 else 0.72


def choose_group_representative(group: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        group,
        key=lambda candidate: (
            -SOURCE_PRIORITY.get(candidate["source_entries"][0]["source"], 0),
            -candidate_confidence(candidate),
            len(candidate["canonical_name"]),
            candidate["canonical_name"],
        ),
    )[0]


def parent_rows_for_used_families(
    groups: list[list[dict[str, Any]]], rules: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    used_family_ids = {group[0]["family_id"] for group in groups}
    family_rules = {item["id"]: item for item in rules["families"] if item["id"] in used_family_ids}
    domain_rules = {
        item["id"]: item
        for item in rules["domains"]
        if item["id"] in {family["domain_id"] for family in family_rules.values()}
    }
    rows: list[dict[str, Any]] = []
    domain_rows: dict[str, dict[str, Any]] = {}
    family_rows: dict[str, dict[str, Any]] = {}
    for domain_id, domain in sorted(domain_rules.items()):
        archetype_id = stable_archetype_id(domain["name"], domain_id, [], "domain")
        row = empty_archetype_row()
        row.update(
            {
                "archetype_id": archetype_id,
                "canonical_name": domain["name"],
                "archetype_level": "domain",
                "short_description": f"Top-level canonical grouping for {domain['name'].casefold()}.",
                "source_count": 0,
                "source_names": json_array(["config/archetype_rules.yaml"]),
                "source_record_ids": "[]",
                "evidence_strength": "organisational",
                "normalisation_method": "rules_config_hierarchy",
                "normalisation_confidence": 1.0,
                "requires_manual_review": False,
                "provenance_json": stable_json(
                    {"rules_version": rules["version"], "domain_id": domain_id}
                ),
            }
        )
        domain_rows[domain_id] = row
        rows.append(row)
    for family_id, family in sorted(family_rules.items()):
        parent = domain_rows[family["domain_id"]]
        archetype_id = stable_archetype_id(family["name"], family_id, [], "business_family")
        row = empty_archetype_row()
        row.update(
            {
                "archetype_id": archetype_id,
                "canonical_name": family["name"],
                "parent_archetype_id": parent["archetype_id"],
                "parent_archetype_name": parent["canonical_name"],
                "archetype_level": "business_family",
                "short_description": f"Business-family grouping for {family['name'].casefold()}.",
                "source_count": 0,
                "source_names": json_array(["config/archetype_rules.yaml"]),
                "source_record_ids": "[]",
                "evidence_strength": "organisational",
                "normalisation_method": "rules_config_hierarchy",
                "normalisation_confidence": 1.0,
                "requires_manual_review": False,
                "provenance_json": stable_json(
                    {
                        "rules_version": rules["version"],
                        "family_id": family_id,
                        "domain_id": family["domain_id"],
                    }
                ),
            }
        )
        family_rows[family_id] = row
        rows.append(row)
    return rows, domain_rows, family_rows


def empty_archetype_row() -> dict[str, Any]:
    row = {column: None for column in ARCHETYPE_COLUMNS}
    for column in (
        "sic2007_codes",
        "sic2007_labels",
        "voa_scat_codes",
        "voa_scat_labels",
        "naics_codes",
        "naics_labels",
        "osm_tags",
        "source_names",
        "source_record_ids",
    ):
        row[column] = "[]"
    row["osm_observed_count_london"] = 0
    row["alias_count"] = 0
    return row


def build_archetypes_and_crosswalk(
    groups: list[list[dict[str, Any]]], rules: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    parent_rows, _, family_rows = parent_rows_for_used_families(groups, rules)
    archetypes = list(parent_rows)
    crosswalk: list[dict[str, Any]] = []
    candidate_to_archetype: dict[str, str] = {}
    for group in groups:
        representative = choose_group_representative(group)
        names = Counter(candidate["canonical_name"] for candidate in group)
        canonical_name = sorted(
            names,
            key=lambda name: (-names[name], len(name), name),
        )[0]
        all_flags = sorted(
            set.intersection(
                *(set(candidate["distinction_flags"]) for candidate in group)
            )
            if len(group) > 1
            else set(group[0]["distinction_flags"])
        )
        family_id = representative["family_id"]
        parent = family_rows[family_id]
        archetype_id = stable_archetype_id(canonical_name, family_id, all_flags)
        for candidate in group:
            candidate_to_archetype[candidate["candidate_id"]] = archetype_id
        entries_by_id: dict[str, dict[str, Any]] = {}
        for candidate in group:
            for entry in candidate["source_entries"]:
                entries_by_id[entry["source_record_id"]] = entry
        entries = [entries_by_id[key] for key in sorted(entries_by_id)]
        source_names = sorted({entry["source"] for entry in entries})
        confidences = [candidate_confidence(candidate) for candidate in group]
        confidence = min(0.99, sum(confidences) / len(confidences) + (0.04 if len(source_names) > 1 else 0))
        evidence_strength = "high" if len(source_names) >= 2 else "medium" if confidence >= 0.82 else "low"
        manual_review = evidence_strength == "low"
        mechanics = representative
        sic_candidates = [candidate for candidate in group if candidate["source_kind"] == "sic"]
        voa_candidates = [candidate for candidate in group if candidate["source_kind"] == "voa"]
        naics_candidates = [candidate for candidate in group if candidate["source_kind"] == "naics"]
        osm_candidates = [candidate for candidate in group if candidate["source_kind"] == "osm"]
        row = empty_archetype_row()
        row.update(
            {
                "archetype_id": archetype_id,
                "canonical_name": canonical_name,
                "parent_archetype_id": parent["archetype_id"],
                "parent_archetype_name": parent["canonical_name"],
                "archetype_level": "actionable",
                "short_description": f"A {canonical_name.casefold()} operation that {mechanics['core_physical_process'].casefold()}.",
                "customer_need": mechanics["customer_need"],
                "customer_type": mechanics["customer_type"],
                "core_physical_process": mechanics["core_physical_process"],
                "revenue_unit": mechanics["revenue_unit"],
                "required_assets": mechanics["required_assets"],
                "typical_location": mechanics["typical_location"],
                "physical_venue": mechanics["physical_venue"],
                "fixed_site_required": mechanics["fixed_site_required"],
                "mobile_operation_possible": mechanics["mobile_operation_possible"],
                "sic2007_codes": json_array(candidate["sic_code"] for candidate in sic_candidates),
                "sic2007_labels": json_array(candidate["original_label"] for candidate in sic_candidates),
                "voa_scat_codes": json_array(candidate["source_code"] for candidate in voa_candidates),
                "voa_scat_labels": json_array(candidate["original_label"] for candidate in voa_candidates),
                "naics_codes": json_array(candidate["source_code"] for candidate in naics_candidates),
                "naics_labels": json_array(candidate["original_label"] for candidate in naics_candidates),
                "osm_tags": json_array(candidate["source_code"] for candidate in osm_candidates),
                "osm_observed_count_london": sum(int(candidate.get("observed_count") or 0) for candidate in osm_candidates),
                "source_count": len(source_names),
                "source_names": json_array(source_names),
                "source_record_ids": json_array(entries_by_id),
                "evidence_strength": evidence_strength,
                "normalisation_method": (
                    "deterministic_exact_merge" if len(group) > 1 else representative["normalisation_method"]
                ),
                "normalisation_confidence": round(confidence, 4),
                "requires_manual_review": manual_review,
                "provenance_json": stable_json(
                    {
                        "build_version": BUILD_VERSION,
                        "rules_version": rules["version"],
                        "candidate_ids": sorted(candidate["candidate_id"] for candidate in group),
                        "family_id": family_id,
                        "domain_id": representative["domain_id"],
                        "distinction_flags": all_flags,
                        "operation_type": representative["operation_type"],
                        "family_assignment_method": representative[
                            "family_assignment_method"
                        ],
                    }
                ),
            }
        )
        archetypes.append(row)
        for entry in entries:
            source_candidate = next(
                candidate
                for candidate in group
                if any(
                    evidence["source_record_id"] == entry["source_record_id"]
                    for evidence in candidate["source_entries"]
                )
            )
            crosswalk.append(
                {
                    "archetype_id": archetype_id,
                    "source": entry["source"],
                    "source_record_id": entry["source_record_id"],
                    "source_code": entry.get("source_code"),
                    "source_label": entry.get("source_label"),
                    "relationship": entry["relationship"],
                    "mapping_confidence": round(candidate_confidence(source_candidate), 4),
                    "mapping_method": (
                        "deterministic_exact_normalisation"
                        if len(group) > 1
                        else "source_candidate_rule"
                    ),
                    "mapping_reason": (
                        "Equivalent cleaned label and compatible mechanics"
                        if len(group) > 1
                        else "Specific source category accepted as a recognisable operation"
                    ),
                    "requires_manual_review": manual_review,
                }
            )
    return archetypes, crosswalk, candidate_to_archetype


def build_aliases(
    archetypes: list[dict[str, Any]],
    crosswalk: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    candidate_to_archetype: Mapping[str, str],
    activity_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    sic_to_archetypes: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        archetype_id = candidate_to_archetype[candidate["candidate_id"]]
        if candidate["sic_code"]:
            sic_to_archetypes[candidate["sic_code"]].add(archetype_id)
    for row in crosswalk:
        alias = compact_text(row["source_label"])
        if alias:
            aliases.append(
                {
                    "archetype_id": row["archetype_id"],
                    "alias": alias,
                    "normalised_alias": normalise_text(alias),
                    "source": row["source"],
                    "source_record_id": row["source_record_id"],
                    "sic_code": row["source_code"] if row["source"] == "uk_sic_2007" else None,
                    "alias_type": "official_label",
                    "confidence": row["mapping_confidence"],
                }
            )
    for activity in activity_rows:
        for archetype_id in sorted(sic_to_archetypes.get(activity["sic_code"], set())):
            aliases.append(
                {
                    "archetype_id": archetype_id,
                    "alias": activity["activity_term"],
                    "normalised_alias": activity["normalised_label"],
                    "source": activity["source"],
                    "source_record_id": activity["source_record_id"],
                    "sic_code": activity["sic_code"],
                    "alias_type": "activity_index_term",
                    "confidence": 0.80,
                }
            )
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in aliases:
        key = (
            row["archetype_id"],
            row["normalised_alias"],
            row["source"],
            row["source_record_id"],
        )
        unique[key] = row
    result = sorted(
        unique.values(),
        key=lambda row: (
            row["archetype_id"],
            row["normalised_alias"],
            row["source"],
            row["source_record_id"],
        ),
    )
    counts = Counter(row["archetype_id"] for row in result)
    for archetype in archetypes:
        archetype["alias_count"] = counts.get(archetype["archetype_id"], 0)
    return result


def unresolved_from_matches(
    matches: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    rows = []
    for match in matches:
        if match["decision"] not in {
            "INSUFFICIENT_INFORMATION",
            "RELATED_BUT_DISTINCT",
            "PARENT_CHILD",
        }:
            continue
        left = candidate_by_id[match["left_candidate_id"]]
        right = candidate_by_id[match["right_candidate_id"]]
        rows.append(
            {
                "unresolved_id": stable_row_id(
                    "unresolved_pair",
                    left["primary_record_id"],
                    right["primary_record_id"],
                    match["decision"],
                ),
                "source_record_id": None,
                "source": f"{left['source_kind']}|{right['source_kind']}",
                "source_code": None,
                "source_label": f"{left['original_label']} <> {right['original_label']}",
                "left_source_record_id": left["primary_record_id"],
                "right_source_record_id": right["primary_record_id"],
                "decision": match["decision"],
                "reason": match["decision_reason"],
                "confidence": round(match["lexical_similarity"], 4),
                "blocking_reasons": match["blocking_reasons"],
            }
        )
    return rows


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with part.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(part, path)


def write_parquet_atomic(
    path: Path, rows: list[dict[str, Any]], schema: pa.Schema
) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty Parquet output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    metadata = {
        b"pipeline": b"scripts/03_build_archetypes.py",
        b"build_version": BUILD_VERSION.encode(),
    }
    table = pa.Table.from_pylist(rows, schema=schema.with_metadata(metadata))
    try:
        pq.write_table(
            table,
            part,
            compression="zstd",
            compression_level=9,
            use_dictionary=True,
            write_statistics=True,
            version="2.6",
        )
        check = pq.ParquetFile(part)
        if check.metadata is None or check.metadata.num_rows != len(rows):
            raise ValueError(f"Parquet verification failed: {path}")
        os.replace(part, path)
    except Exception:
        part.unlink(missing_ok=True)
        raise


def mark_manual_review_from_unresolved(
    archetypes: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    crosswalk: list[dict[str, Any]],
) -> None:
    source_to_arch = {row["source_record_id"]: row["archetype_id"] for row in crosswalk}
    flagged = {
        source_to_arch[source_id]
        for row in unresolved
        for source_id in (row.get("left_source_record_id"), row.get("right_source_record_id"))
        if source_id in source_to_arch
    }
    for row in archetypes:
        if row["archetype_id"] in flagged:
            row["requires_manual_review"] = True
    for row in crosswalk:
        if row["archetype_id"] in flagged:
            row["requires_manual_review"] = True


def archetype_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    order = {"domain": 0, "business_family": 1, "actionable": 2}
    return (order[row["archetype_level"]], normalise_text(row["canonical_name"]), row["archetype_id"])


def make_review_sample(
    archetypes: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    crosswalk: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actionable = [row for row in archetypes if row["archetype_level"] == "actionable"]
    source_to_archetype = {
        row["source_record_id"]: row["archetype_id"] for row in crosswalk
    }
    related_archetype_ids = {
        source_to_archetype[source_record_id]
        for item in unresolved
        if item["decision"] == "RELATED_BUT_DISTINCT"
        for source_record_id in (
            item.get("left_source_record_id"),
            item.get("right_source_record_id"),
        )
        if source_record_id in source_to_archetype
    }
    strata = {
        "high_confidence_cross_source": sorted(
            [row for row in actionable if row["source_count"] >= 2 and row["evidence_strength"] == "high"],
            key=archetype_sort_key,
        ),
        "single_source": sorted(
            [row for row in actionable if row["source_count"] == 1], key=archetype_sort_key
        ),
        "high_osm_count": sorted(
            [row for row in actionable if row["osm_observed_count_london"] > 0],
            key=lambda row: (-row["osm_observed_count_london"], row["archetype_id"]),
        ),
        "low_confidence_mapping": sorted(
            [row for row in actionable if row["requires_manual_review"]], key=archetype_sort_key
        ),
        "parent_child_case": sorted(actionable, key=lambda row: (row["parent_archetype_name"], row["canonical_name"])),
        "related_but_distinct": sorted(
            [row for row in actionable if row["archetype_id"] in related_archetype_ids],
            key=archetype_sort_key,
        ),
    }
    quotas = {
        "high_confidence_cross_source": 18,
        "single_source": 22,
        "high_osm_count": 18,
        "low_confidence_mapping": 17,
        "parent_child_case": 15,
        "related_but_distinct": 10,
    }
    chosen: set[str] = set()
    sample: list[dict[str, Any]] = []
    for stratum, quota in quotas.items():
        for row in strata[stratum]:
            if row["archetype_id"] in chosen:
                continue
            chosen.add(row["archetype_id"])
            sample.append(review_row(stratum, row))
            if sum(item["review_stratum"] == stratum for item in sample) >= quota:
                break
    if len(sample) < 100:
        for row in sorted(actionable, key=archetype_sort_key):
            if row["archetype_id"] not in chosen:
                chosen.add(row["archetype_id"])
                sample.append(review_row("deterministic_fill", row))
            if len(sample) >= 100:
                break
    return sorted(sample, key=lambda row: (row["review_stratum"], row["canonical_name"], row["archetype_id"]))


def review_row(stratum: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "review_stratum": stratum,
        "archetype_id": row["archetype_id"],
        "canonical_name": row["canonical_name"],
        "parent_archetype_name": row["parent_archetype_name"],
        "source_count": row["source_count"],
        "evidence_strength": row["evidence_strength"],
        "normalisation_confidence": row["normalisation_confidence"],
        "requires_manual_review": row["requires_manual_review"],
        "osm_observed_count_london": row["osm_observed_count_london"],
        "source_names": row["source_names"],
        "source_record_ids": row["source_record_ids"],
        "review_notes": "",
    }


def write_build_report(
    *,
    command: str,
    state: BuildState,
    candidates: list[dict[str, Any]],
    archetypes: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
    crosswalk: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    review: list[dict[str, Any]],
) -> None:
    actionable = [row for row in archetypes if row["archetype_level"] == "actionable"]
    parents = [row for row in archetypes if row["archetype_level"] != "actionable"]
    merged = next((row for row in actionable if row["source_count"] >= 2), None)
    related = next((row for row in unresolved if row["decision"] == "RELATED_BUT_DISTINCT"), None)
    infrastructure = next((row for row in excluded if row["rule_id"] in {"infrastructure", "osm_non_business"}), None)
    unresolved_example = next((row for row in unresolved if row["decision"] == "INSUFFICIENT_INFORMATION"), None)
    lines = [
        "# Canonical archetype build report",
        "",
        f"Build version: `{BUILD_VERSION}`",
        f"Exact command: `{command}`",
        "Mode: deterministic no-LLM; embedding similarity remains null and ambiguous pairs are routed to review.",
        "",
        "## Totals",
        "",
        f"- Input records examined: {state.input_record_count:,}",
        f"- Candidate operation records: {len(candidates):,}",
        f"- Canonical archetypes: {len(archetypes):,}",
        f"- Actionable archetypes: {len(actionable):,}",
        f"- Parent categories: {len(parents):,}",
        f"- Aliases: {len(aliases):,}",
        f"- Crosswalk mappings: {len(crosswalk):,}",
        f"- Candidate match pairs: {len(matches):,}",
        f"- Excluded records: {len(excluded):,}",
        f"- Unresolved records or pairs: {len(unresolved):,}",
        f"- Manual-review archetypes: {sum(bool(row['requires_manual_review']) for row in actionable):,}",
        f"- Review sample rows: {len(review):,}",
        "",
        "## Source use",
        "",
        "- SIC classes/subclasses, NAICS national industries, VOA SCat categories and aggregated OSM primary tags generated operation candidates.",
        "- SIC activity-index records generated aliases and search vocabulary only.",
        "- OSM secondary-tag combinations were retained as provenance examples, not promoted to separate archetypes.",
        "",
        "## Auditable adjudication examples",
        "",
    ]
    if merged:
        lines.append(
            f"- Equivalence merge: **{merged['canonical_name']}** combines {merged['source_count']} sources through exact cleaned-label/override rules with compatible mechanics."
        )
    if related:
        lines.append(
            f"- Related but distinct: `{related['source_label']}` — {related['reason']}"
        )
    if actionable:
        child = actionable[0]
        lines.append(
            f"- Parent-child: **{child['parent_archetype_name']}** → **{child['canonical_name']}**."
        )
    if infrastructure:
        lines.append(
            f"- Excluded infrastructure: `{infrastructure['source_label']}` — {infrastructure['exclusion_reason']}."
        )
    if unresolved_example:
        lines.append(
            f"- Unresolved: `{unresolved_example['source_label']}` — {unresolved_example['reason']}."
        )
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            *[f"- `{path.relative_to(REPO_ROOT)}`" for path in OUTPUT_PATHS.values()],
            "",
            "## Boundaries",
            "",
            "No financial estimates, automation percentages, synthetic business variants, Companies House records or recommendations were introduced.",
            "",
        ]
    )
    part = OUTPUT_PATHS["report"].with_name(OUTPUT_PATHS["report"].name + ".part")
    part.write_text("\n".join(lines), encoding="utf-8")
    os.replace(part, OUTPUT_PATHS["report"])


def validate_existing_outputs() -> dict[str, int]:
    if not all(path.is_file() for path in OUTPUT_PATHS.values()):
        raise FileNotFoundError("one or more canonical build outputs are missing")
    with OUTPUT_PATHS["archetypes"].open(newline="", encoding="utf-8") as handle:
        archetypes = list(csv.DictReader(handle))
    if not archetypes or len({row["archetype_id"] for row in archetypes}) != len(archetypes):
        raise ValueError("existing archetype CSV is empty or has duplicate IDs")
    counts = {"archetypes": len(archetypes)}
    for name in ("crosswalk", "aliases", "matches"):
        metadata = pq.ParquetFile(OUTPUT_PATHS[name]).metadata
        if metadata is None or metadata.num_rows <= 0:
            raise ValueError(f"existing {name} Parquet is empty")
        counts[name] = metadata.num_rows
    for name in ("unresolved", "excluded", "review"):
        with OUTPUT_PATHS[name].open(newline="", encoding="utf-8") as handle:
            counts[name] = sum(1 for _ in csv.DictReader(handle))
    return counts


def build_catalogue(command: str) -> dict[str, int]:
    rules = load_rules()
    tables, source_record_ids = read_intermediates()
    state = extract_candidates(tables, rules)
    if state.source_record_ids != source_record_ids:
        raise AssertionError("source record universe changed during extraction")
    candidates = enrich_candidates(state.candidates, rules)
    matches = build_match_candidates(candidates, rules)
    groups = group_candidates(candidates, matches)
    archetypes, crosswalk, candidate_to_archetype = build_archetypes_and_crosswalk(
        groups, rules
    )
    aliases = build_aliases(
        archetypes,
        crosswalk,
        candidates,
        candidate_to_archetype,
        state.activity_rows,
    )
    unresolved = state.unresolved + unresolved_from_matches(matches, candidates)
    unresolved = sorted(
        {row["unresolved_id"]: row for row in unresolved}.values(),
        key=lambda row: row["unresolved_id"],
    )
    excluded = sorted(
        {row["source_record_id"]: row for row in state.excluded}.values(),
        key=lambda row: (row["source"], row["source_record_id"]),
    )
    mark_manual_review_from_unresolved(archetypes, unresolved, crosswalk)
    archetypes.sort(key=archetype_sort_key)
    crosswalk.sort(
        key=lambda row: (row["archetype_id"], row["source"], row["source_record_id"])
    )
    match_output = [
        {field.name: row.get(field.name) for field in MATCH_SCHEMA}
        for row in sorted(
            matches,
            key=lambda row: (
                row["left_source_record_id"],
                row["right_source_record_id"],
            ),
        )
    ]
    review = make_review_sample(archetypes, unresolved, crosswalk)

    actionable_count = sum(row["archetype_level"] == "actionable" for row in archetypes)
    if actionable_count < 500:
        raise ValueError(f"only {actionable_count} actionable archetypes were produced")
    write_csv_atomic(OUTPUT_PATHS["archetypes"], archetypes, ARCHETYPE_COLUMNS)
    write_parquet_atomic(OUTPUT_PATHS["crosswalk"], crosswalk, CROSSWALK_SCHEMA)
    write_parquet_atomic(OUTPUT_PATHS["aliases"], aliases, ALIAS_SCHEMA)
    write_parquet_atomic(OUTPUT_PATHS["matches"], match_output, MATCH_SCHEMA)
    write_csv_atomic(OUTPUT_PATHS["unresolved"], unresolved, UNRESOLVED_COLUMNS)
    write_csv_atomic(OUTPUT_PATHS["excluded"], excluded, EXCLUDED_COLUMNS)
    write_csv_atomic(OUTPUT_PATHS["review"], review, REVIEW_COLUMNS)
    write_build_report(
        command=command,
        state=state,
        candidates=candidates,
        archetypes=archetypes,
        aliases=aliases,
        crosswalk=crosswalk,
        matches=matches,
        excluded=excluded,
        unresolved=unresolved,
        review=review,
    )
    return {
        "input_records": state.input_record_count,
        "candidates": len(candidates),
        "archetypes": len(archetypes),
        "actionable": actionable_count,
        "parents": len(archetypes) - actionable_count,
        "aliases": len(aliases),
        "crosswalk": len(crosswalk),
        "excluded": len(excluded),
        "unresolved": len(unresolved),
        "manual_review": sum(
            row["archetype_level"] == "actionable" and row["requires_manual_review"]
            for row in archetypes
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--skip-existing", action="store_true")
    group.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = shlex.join([sys.executable, *sys.argv])
    if args.skip_existing:
        try:
            counts = validate_existing_outputs()
        except Exception:
            pass
        else:
            print(
                f"Existing catalogue is valid: {counts['archetypes']:,} rows; no files rewritten"
            )
            return 0
    try:
        counts = build_catalogue(command)
    except Exception as exc:
        print(f"Archetype build failed: {exc}", file=sys.stderr)
        return 1
    for key, value in counts.items():
        print(f"{key}: {value:,}")
    print(f"Report: {OUTPUT_PATHS['report'].relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
