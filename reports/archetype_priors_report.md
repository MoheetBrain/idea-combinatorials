# Archetype financial, labour-count, and risk priors

These are broad statistical priors, not claims about a particular site or operator.

## Coverage

| Measure | Count | Coverage |
|---|---:|---:|
| Actionable archetypes | 2,369 | 100.0% |
| Financial prior | 1,935 | 81.7% |
| Labour prior | 1,967 | 83.0% |
| Risk prior | 1,925 | 81.3% |

## Mapping

- Exact SIC mappings: 343
- Parent SIC fallbacks: 264
- Broad domain fallbacks (grade D, manual review): 1,377
- Unresolved review rows: 488

## Industry tables

- Raw financial observations: 16,819; financial priors: 761
- Raw employment observations: 1,890; labour priors: 1,592
- Raw risk observations: 733; risk priors: 348
- Archetype-prior mappings: 5,896

## Derivation safeguards

- `turnover_per_reporting_unit = ABS turnover / ABS reporting-unit count`; it is not site revenue.
- `operating_surplus_proxy = aGVA - employment costs`; it is explicitly not operating profit, EBITDA, or net profit.
- Enterprise/local-unit ratios combine ABS 2024 with UK Business 2025 only where shown, with an explicit compatibility flag and confidence penalty.
- Employment per local unit combines BRES 2024 Great Britain employment with UK Business 2025 United Kingdom local units; the geography/year mismatch remains visible.
- Survival uses the 2019 birth cohort; current births/deaths/stock use 2024; insolvency rates use England and Wales 2025.
- Missing values remain null. No LLM or unsupported synthetic financial value is used.

## Uncertainty

Observed turnover-size bands supply interpolated P10/P90 bounds when the quantile lies in a closed band. Otherwise published coefficients of variation support a twice-CV sampling interval. Open-ended quantiles and metrics without a defensible uncertainty measure remain null.

## Outputs

- financial: `data/processed/industry_financial_priors_v0.parquet`
- labour: `data/processed/industry_labour_priors_v0.parquet`
- risk: `data/processed/industry_risk_priors_v0.parquet`
- archetype: `data/processed/archetype_priors_v0.parquet`
- crosswalk: `data/processed/archetype_prior_crosswalk_v0.parquet`
- unresolved: `data/processed/unresolved_prior_mappings_v0.csv`
- report: `reports/archetype_priors_report.md`
- review: `reports/archetype_priors_review_sample.csv`

Exact command: `/Users/moheet/idea_combinatorials/idea-combinatorials/.venv/bin/python scripts/07_build_archetype_priors.py --force`
