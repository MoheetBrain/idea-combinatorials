# Occupation Source Download Report

| Source | Release | File | Size | Validation | SHA-256 |
|---|---:|---|---:|---|---|
| National Employment Matrix 2024-34 | 2024-34 | `data/raw/occupation/bls_national_employment_matrix_2024_2034.html.zip` | 15611857 | passed | `454fc2ec778151b2` |
| O*NET production database | 30.3 | `data/raw/occupation/onet_database.csv.zip` | 16258072 | passed | `1a07f893d037ee81` |
| ESCO to O*NET-SOC crosswalk | current | `data/raw/occupation/esco_to_onet_soc.xlsx` | 213061 | passed | `bf6c854f7ffc935e` |
| O*NET-SOC 2019 to 2018 SOC crosswalk | current | `data/raw/occupation/onet_soc2019_to_soc2018.xlsx` | 44847 | passed | `bfc6722a92c2d607` |
| Extended SOC 2020 structure and descriptions | SOC 2020 | `data/raw/occupation/ons_extended_soc2020.xlsx` | 488138 | passed | `199922335efb6dcf` |
| SOC 2020 Volume 2 coding index | SOC 2020 | `data/raw/occupation/ons_soc2020_coding_index.xlsx` | 3768655 | passed | `a6e27c94fd18c7fe` |
| SOC 2020 Volume 1 structure and descriptions | SOC 2020 | `data/raw/occupation/ons_soc2020_volume1.xlsx` | 281579 | passed | `dd29e6b960d8f2d7` |
| GPTs are GPTs data and code | 0471612fef3cc22b74fb884d27bff9dbd3770582 | `data/raw/automation/openai_gpts_are_gpts.zip` | 9199738 | passed | `bab4dc2da629786d` |

Official files validated: **8**

Exact command:

```text
scripts/09_download_occupation_sources.py --skip-existing
```
