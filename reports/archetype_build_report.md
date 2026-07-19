# Canonical archetype build report

Build version: `1`
Exact command: `/Users/moheet/idea_combinatorials/idea-combinatorials/.venv/bin/python scripts/03_build_archetypes.py --force`
Mode: deterministic no-LLM; embedding similarity remains null and ambiguous pairs are routed to review.

## Totals

- Input records examined: 41,833
- Candidate operation records: 2,404
- Canonical archetypes: 2,420
- Actionable archetypes: 2,369
- Parent categories: 51
- Aliases: 31,222
- Crosswalk mappings: 3,923
- Candidate match pairs: 683
- Excluded records: 2,611
- Unresolved records or pairs: 1,175
- Manual-review archetypes: 929
- Review sample rows: 100

## Source use

- SIC classes/subclasses, NAICS national industries, VOA SCat categories and aggregated OSM primary tags generated operation candidates.
- SIC activity-index records generated aliases and search vocabulary only.
- OSM secondary-tag combinations were retained as provenance examples, not promoted to separate archetypes.

## Auditable adjudication examples

- Equivalence merge: **Advertising Agencies** combines 2 sources through exact cleaned-label/override rules with compatible mechanics.
- Related but distinct: `Support Activities for Metal Mining  <> Support activities for crop production` — Related vocabulary does not establish equivalent operating mechanics
- Parent-child: **Specialist retail** → **3d Printing shop**.
- Excluded infrastructure: `Highway, Street, and Bridge Construction ` — Infrastructure or non-operational property category.
- Unresolved: `shop=electrical_supplies` — Single OSM observation without corroborating taxonomy evidence.

## Outputs

- `data/processed/archetypes_v0.csv`
- `data/processed/taxonomy_crosswalk_v0.parquet`
- `data/processed/archetype_aliases_v0.parquet`
- `data/processed/unresolved_categories_v0.csv`
- `data/processed/excluded_categories_v0.csv`
- `data/interim/archetype_match_candidates_v0.parquet`
- `reports/archetype_build_report.md`
- `reports/archetype_review_sample.csv`

## Boundaries

No financial estimates, automation percentages, synthetic business variants, Companies House records or recommendations were introduced.
