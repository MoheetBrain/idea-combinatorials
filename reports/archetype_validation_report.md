# Archetype catalogue validation report

Validation status: **PASS**
Checks passed: 28/28

## Catalogue counts

- Input Records: 41,833
- Archetypes: 2,420
- Actionable: 2,369
- Parents: 51
- Aliases: 31,222
- Crosswalk: 3,923
- Matches: 683
- Excluded: 2,611
- Unresolved: 1,175
- Manual Review: 929
- Review: 100

## Validation checks

| Check | Status | Evidence |
|---|---:|---|
| exact archetype schema | PASS | 32 columns found |
| no financial or automation fields | PASS | forbidden fields: none |
| positive catalogue | PASS | 2,420 rows |
| unique archetype IDs | PASS | 0 duplicates |
| non-empty canonical names | PASS | all rows checked |
| valid hierarchy levels | PASS | domain/family/actionable only |
| minimum actionable catalogue | PASS | 2,369 actionable rows |
| actionable customer needs | PASS | all actionable rows |
| actionable core processes | PASS | all actionable rows |
| valid provenance JSON | PASS | 0 invalid rows |
| valid parent IDs | PASS | 0 dangling IDs |
| acyclic hierarchy | PASS | no cycle |
| stable deterministic IDs | PASS | 0 mismatches |
| crosswalk referential integrity | PASS | 0 errors |
| actionable provenance mappings | PASS | 0 missing mappings |
| CSV-to-crosswalk provenance equality | PASS | 0 inconsistent rows |
| alias referential integrity | PASS | 0 invalid aliases |
| actionable alias coverage | PASS | 0 actionable rows without aliases |
| confidence bounds | PASS | 0 invalid values |
| no actionable SIC Sections | PASS | 0 violations |
| distinct operating models preserved | PASS | 1 reference pairs observed; 0 collapsed |
| no synthetic AI/robot combinations | PASS | 0 suspect rows |
| deterministic archetype sort | PASS | level/name/ID order |
| deterministic crosswalk sort | PASS | archetype/source/record order |
| deterministic alias sort | PASS | archetype/alias/source/record order |
| Parquet readability | PASS | crosswalk, aliases and match candidates |
| review sample coverage | PASS | 100 rows |
| review sample strata | PASS | strata: ['high_confidence_cross_source', 'high_osm_count', 'low_confidence_mapping', 'parent_child_case', 'related_but_distinct', 'single_source'] |

## Scope boundary

This audit covers catalogue structure, provenance, hierarchy, deterministic identifiers, operating-model safeguards and readable outputs. It does not add financial, automation or synthetic-business data.
