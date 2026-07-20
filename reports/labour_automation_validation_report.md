# Labour and Automation Validation Report

**Validation status: PASS**

## Coverage

- Official source payloads: **8**
- Source occupation records: **8,143**
- Occupation crosswalk mappings: **11,540**
- Industry-occupation observations: **67,699**
- Occupation-task links: **18,796**
- Task-capability evidence rows: **187,960**
- Archetypes with occupation mixes: **1,931/2,369 (81.5%)**
- Mapping methods: `{'archetype_rule_override': 1, 'business_family_fallback': 1095, 'exact_detailed_naics': 45, 'parent_naics': 790, 'unmapped': 438}`
- Archetypes with task bundles: **1,931**
- Archetypes with an end-to-end opportunity index: **1,883**
- Defensible payroll-saving estimates: **0**
- Manual-review rows: **2,369**

## Validation checks

| Check | Status | Evidence |
|---|---:|---|
| Official source validation | PASS | 8 manifest rows |
| Positive output row counts | PASS | {'ontology': 8143, 'crosswalks': 11540, 'industry_observations': 67699, 'tasks': 18796, 'capability': 187960, 'industry_mix': 67699, 'archetype_mix': 503826, 'occupation_automation': 744, 'archetype_tasks': 108002, 'opportunity': 2369} |
| Unique occupation identifiers | PASS | 8,143 rows |
| Unique occupation-task links | PASS | 18,796 links |
| Unique capability evidence IDs | PASS | 187,960 rows |
| Unique archetype opportunities | PASS | 2,369 rows |
| Crosswalk target integrity | PASS | 0 missing endpoints |
| One-to-many mappings preserved | PASS | 2,328 one-to-many source mappings |
| Industry shares finite and non-negative | PASS | 0 invalid shares |
| Complete industry shares sum approximately to one | PASS | 0 invalid complete groups of 422 |
| Industry shares not silently renormalised | PASS | Published counts retained; completeness flag stored |
| capability score ranges | PASS | 0 values outside [0,1] |
| occupation_automation score ranges | PASS | 0 values outside [0,1] |
| opportunity score ranges | PASS | 0 values outside [0,1] |
| Technology modalities remain separate | PASS | Counter({'computer_vision': 18796, 'fixed_environment_robotics': 18796, 'identity_and_access_automation': 18796, 'llm_assistance': 18796, 'predictive_maintenance': 18796, 'rules_based_rpa': 18796, 'scheduling_and_optimisation': 18796, 'software_agent_execution': 18796, 'speech_interface': 18796, 'unstructured_environment_robotics': 18796}) |
| Unknown rubric values remain explicit | PASS | At least one evidence row preserves named unknown dimensions |
| Task importance is not time share | PASS | Every task carries an explicit proxy disclaimer |
| Derived values retain provenance | PASS | All rows with a provenance field contain valid JSON |
| Grade F prohibited | PASS | No unsupported-synthetic evidence grades |
| All actionable archetypes represented | PASS | 2,369/2,369 |
| Archetype occupation links valid | PASS | 1,931 linked archetypes |
| Occupation-mix coverage at least 70% | PASS | 81.5% |
| Mapped archetypes receive task bundles | PASS | 1,931/1,931 |
| No unsupported payroll-saving estimates | PASS | 0 rows |
| Headcount results include staffing constraints | PASS | 0 incomplete rows |
| Residual-human outputs explicit | PASS | 0 malformed rows |
| Ambiguous US-to-UK mappings not forced | PASS | 0 over-broad mappings |
| Ontology deterministic order | PASS | source taxonomy/code |
| Industry mix deterministic order | PASS | industry/occupation code |
| Opportunity deterministic order | PASS | archetype ID |
| Review sample size and anchors | PASS | 100 rows |

## Kill criteria

| Criterion | Status | Evidence |
|---|---:|---|
| Operational plausibility | CLEAR | 0/10 anchor mixes failed a conservative role-presence check: [] |
| Physical/manual tasks not labelled LLM-executable | CLEAR | 0 violations |
| Exposure not converted to payroll reduction | CLEAR | 0 payroll rows |
| Minimum staffing and shift constraints present | CLEAR | 0 incomplete headcount rows |
| Ambiguous occupation bridges not forced | CLEAR | 0 forced mappings |
| Task importance not represented as hours | CLEAR | Explicit proxy labelling |
| Position reducibility is explainable | CLEAR | 0 unexplained rows |
| Residual human responsibilities retained | CLEAR | 0 malformed rows |
| Attractive scores not driven by unflagged weak fallbacks | CLEAR | 0 violations |

## Interpretation safeguards

- Published LLM exposure remains a research exposure signal; it is not an adoption, unattended-execution, job-loss, or payroll-saving measure.
- O*NET importance/relevance weights remain labelled proxies and are not working-time shares.
- US BLS occupation shares are explicitly graded geographic transfers to the UK.
- Numerical staffing floors and payroll-saving fractions remain null without establishment-level shift and safe-staffing evidence.
- The review sample is a deterministic quality-control artefact; rows marked manual review are not accepted as site-level staffing models.

## Current output hashes

- `archetype_mix`: `53b7f8ca6be9ac79ed6e932aa9ebe27a547b4bbbc03d8013a79b22ea11be15c0`
- `archetype_tasks`: `70ecc8ccf082dc424a564b034f6aadae739951d4276ac3533501d9b2c0ef1967`
- `capability`: `7cbbd15a6628b9cd490597efb4f382dc4a365f6b979df7952c2fec3c43f8200c`
- `crosswalks`: `5e37fd6a7d2ce89cad94971b651e4579eeb3318a8fee860c26c3a53bf6ed5efa`
- `industry_mix`: `d8f851f88f4a363f34370d20af2a6496b0915115d0c57fca62f33b22f344cc1f`
- `industry_observations`: `6ebf1d0fef0e11188537b0a5d4f816f7876a7ab0b4bc562ad19cfab864d3c90d`
- `occupation_automation`: `160c525193259e12bab1fd17bb3cc0b8e699ed68c20832061df14f5347f90d19`
- `ontology`: `6d029fea7b1bcd2cda5449858ddb6f6421a384b1f185abdbc276869acca50b25`
- `opportunity`: `7579ebbeecc2757679798ff286e841b381cec4368082c1f7e1629dd792c92ac2`
- `tasks`: `99197596ba04f224072a8a59497c94a29f6ae5828f16a67d857e0f4712054717`
