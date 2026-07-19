# Enrichment source download report

Generated: `2026-07-19T18:53:31Z`

| Source | Year | Status | Size | Validation | Saved path |
|---|---:|---|---:|---|---|
| Annual Business Survey 2024: employment size bands | 2024 | already_valid | 153,670 bytes | passed | `data/raw/financial/abs_2024_employment_size_band.xlsx` |
| Annual Business Survey 2024: UK quality measures | 2024 | already_valid | 1,454,525 bytes | passed | `data/raw/financial/abs_2024_quality_measures.xlsx` |
| Annual Business Survey 2024: regional Sections A to S | 2024 | already_valid | 2,667,098 bytes | passed | `data/raw/financial/abs_2024_regional_sections.xlsx` |
| Annual Business Survey 2024: regional quality measures | 2024 | already_valid | 1,753,949 bytes | passed | `data/raw/financial/abs_2024_regional_quality.xlsx` |
| Annual Business Survey 2024: Sections A to S | 2024 | already_valid | 1,055,873 bytes | passed | `data/raw/financial/abs_2024_sections.xlsx` |
| ASHE 2025 provisional: industry by occupation, table 29 | 2025 | already_valid | 14,256,925 bytes | passed | `data/raw/labour/ashe_table29_2025_provisional.zip` |
| ASHE 2025 provisional: region by industry, table 5 | 2025 | already_valid | 25,232,585 bytes | passed | `data/raw/labour/ashe_table5_2025_provisional.zip` |
| BRES 2024 provisional: coefficients of variation | 2024 | already_valid | 155,143 bytes | passed | `data/raw/labour/bres_2024_cv.xlsx` |
| BRES 2024 provisional: industry table 2 | 2024 | already_valid | 200,702 bytes | passed | `data/raw/labour/bres_2024_provisional.xlsx` |
| Business demography 2024 reference tables | 2024 | already_valid | 926,112 bytes | passed | `data/raw/risk/business_demography_2024.xlsx` |
| Business insolvency demography 2015 to 2025 | 2015-2025 | already_valid | 243,426 bytes | passed | `data/raw/risk/business_insolvency_demography_2015_2025.xlsx` |
| Company insolvency statistics: June 2026 | 2026 | already_valid | 491,784 bytes | passed | `data/raw/risk/company_insolvencies_2026_06.xlsx` |
| Company insolvency statistics: June 2026 industry tables | 2026 | already_valid | 1,636,952 bytes | passed | `data/raw/risk/company_insolvencies_industry_2026_06.xlsx` |
| UK Business: activity, size and location 2025 | 2025 | already_valid | 1,050,564 bytes | passed | `data/raw/financial/uk_business_2025.xlsx` |

## Summary

- Sources validated: 14 of 14
- Total validated disk usage: 51,279,308 bytes
- Failures: 0
- Manifest: `data/enrichment_source_manifest.csv`
- Exact command: `/Users/moheet/idea_combinatorials/idea-combinatorials/.venv/bin/python scripts/05_download_enrichment_sources.py --skip-existing`

## Failures

None.

## Scope notes

- Foreign calibration sources were not downloaded: the required UK sources are machine-readable and are preferred for this phase.
- ABS monetary units are not normalised during acquisition; workbook-specific units are interpreted and recorded by the parser.
- Reporting units, enterprises, and local units remain distinct populations.
