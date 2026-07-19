# Taxonomy parse report

Parser version: `3`
Exact command: `/Users/moheet/idea_combinatorials/idea-combinatorials/.venv/bin/python scripts/02_parse_taxonomies.py --skip-existing`
Manifest-validated required inputs: 6
Foursquare status: `manual_required` (optional; not parsed)

| Source | Input file | Input sheets or structures inspected | Records parsed | Duplicates | Malformed records | Unmatched codes | Output path | Output size | Runtime | Validation status |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---|
| UK SIC hierarchy | `data/raw/sic/uk_sic_2007_summary_structure.xlsx` | reworked structure, Sheet2, Sheet3 | 1187 | 0 | 0 | 0 | `data/interim/sic_hierarchy.parquet` | 96.06 KiB | 0.023s | passed |
| UK SIC activity index | `data/raw/sic/uk_sic_2007_activity_index_2024.xlsx` | Addendum 1 - Reclassified, Addendum 2 - Additions, Addendum 3 - Deletions, Addendum 4  Typographic Amend's, Alphabetical Index, Numerical Index | 32645 | 7 | 0 | 0 | `data/interim/sic_activity_index.parquet` | 1.65 MiB | 0.010s | passed |
| VOA Special Categories | `data/raw/voa/voa_scat_revaluation_2026.xlsx` | Cover, Contents, Notes, RVL_1_1, RVL_1_2, RVL_2_1, RVL_2_2, RVL_2_3, RVL_2_4, RVL_2_5, RVL_3_1, RVL_3_2, RVL_3_3, RVL_3_4, RVL_3_5, RVL_4_1, RVL_4_2, RVL_4_3, RVL_5_1, RVL_5_2, RVL_5_3 | 1152 | 0 | 0 | 0 | `data/interim/voa_scat.parquet` | 125.06 KiB | 0.001s | passed |
| NAICS hierarchy | `data/raw/naics/naics_2022_structure.xlsx` | 2022 NAICS Structure | 2125 | 0 | 0 | 0 | `data/interim/naics_hierarchy.parquet` | 137.34 KiB | 0.001s | passed |
| NAICS descriptions | `data/raw/naics/naics_2022_descriptions.xlsx` | 2022_NAICS_Descriptions | 2125 | 0 | 0 | 0 | `data/interim/naics_descriptions.parquet` | 438.81 KiB | 0.001s | passed |
| Greater London OpenStreetMap | `data/raw/osm/greater-london-latest.osm.pbf` | OSM PBF nodes, ways, and relations, tag keys: amenity, shop, leisure, tourism, office, craft, healthcare, sport, man_made, industrial, landuse | 2599 | 0 | 0 | 0 | `data/interim/osm_business_categories.parquet` | 182.33 KiB | 0.001s | passed |

## Record totals

- UK SIC hierarchy records: 1,187
- UK SIC activity terms: 32,645
- VOA SCat records: 1,152 across 384 distinct SCat codes
- NAICS hierarchy records: 2,125
- NAICS description records: 2,125
- Distinct OSM tag combinations: 2,599
- OSM physical venue observations: 184,460
- Total intermediate records: 41,833

## Records by hierarchy level or partition

- UK SIC hierarchy: Class=615, Division=88, Group=272, Section=21, Subclass=191
- UK SIC activity index: alphabetical=15,957, numerical=16,688
- VOA Special Categories: England=384, England and Wales=384, Wales=384
- NAICS hierarchy: industry_group=308, naics_industry=689, national_industry=1,012, sector=20, subsector=96
- NAICS descriptions: industry_group=308, naics_industry=689, national_industry=1,012, sector=20, subsector=96
- Greater London OpenStreetMap: amenity=761, craft=93, healthcare=33, industrial=48, landuse=9, leisure=529, man_made=11, office=224, shop=698, sport=130, tourism=63

## Warnings

- The SCat tables provide counts and rateable values but no SCat-level floorspace field; floorspace remains null.
- Suppressed [c] and not-applicable [z] metrics remain null and are preserved in provenance_json.
- OSM inclusion is tag-rule based; generic and multi-purpose combinations require canonicalisation review.

## Parser failures

None.

## Ambiguities for canonicalisation

- UK SIC activity codes are five-digit leaf codes; unsplit hierarchy classes retain their official four-digit class code plus a separate five-digit `leaf_code`.
- Alphabetical and numerical SIC index records remain separate through `index_type`; only exact within-index duplicates are removed.
- VOA emits one record per SCat and geography (England and Wales, England, Wales). Suppressed metrics remain null rather than being estimated.
- NAICS title text, including the source workbook's trilateral `T` marker, is preserved exactly.
- OSM combinations reflect observed tagging practice, not equivalence; generic, secondary, and multi-purpose tags require review before merging.
- Foursquare remains optional and was not awaited.
