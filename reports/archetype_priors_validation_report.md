# Archetype priors validation report

Status: **PASSED**

Generated: `2026-07-19T18:55:29+00:00`

## Coverage and integrity

| Check | Result |
|---|---:|
| Actionable catalogue rows | 2,369 |
| Archetype prior rows | 2,369 |
| Financial coverage | 81.7% |
| Labour coverage | 83.0% |
| Risk coverage | 81.3% |
| Exact financial SIC mappings | 343 |
| Parent financial SIC mappings | 264 |
| Broad domain financial fallbacks | 1,377 |
| Archetypes without financial priors | 434 |
| Manual-review rows | 1,994 |
| High-confidence rows | 325 |
| Low-confidence rows | 1,391 |

## Validation performed

- Source manifests, hashes, licences, units, years, and geographies are present.
- SIC codes remain strings and leading-zero codes survive.
- Ratios, survival values, confidence values, intervals, and finite-number constraints pass.
- Every mapped value links to a current actionable archetype and mapping crosswalk.
- Operating-surplus proxy warnings explicitly reject profit/EBITDA/net-profit interpretations.
- No monthly decision metrics, automation scoring, Grade F values, or LLM financial values are present.
- Deterministic order and a stratified review sample of at least 100 rows are enforced.

## Output hashes

- `archetypes`: `e0ef56dfd874c2506bcac17f1edffd32dfd7fc6f2118f6767158be3bc8b1a1ba`
- `crosswalk`: `948be78476bd62e04e67603bd7657aa07f1bc3f6af0790ca027618419e08c31c`
- `financial`: `6f5c82ecfc445824c4a3261994b7653f0d83c14b79b3aa4ad2ed05e6c757ed32`
- `labour`: `a3df62b952b19e2ec4a3cae1d6cd5873dbe2280fdeaa3e53e71c1a4c85052154`
- `manifest`: `7a24c8bb45903be818e8ff7663523fbb271247989d03bcc1b3a039e9691e6436`
- `priors`: `eaa025b9a800f1e24c487f9a1305a03defba2c87a351053bbb74a95b13b8f6fa`
- `review`: `84722fd3033fe7d91b3827700cc39c110fd45b45d7ab08a8496407b13a029807`
- `risk`: `18585620be46faebfa0e32c642fd6bdc875c85c54e8473bf379aeff4a99d63d1`
- `unresolved`: `58fefc31cf9325832d23d5a5d621a98899fb1f6ca9baea0de4f1a5b2aaa187a9`

## Errors

None.

## Warnings

None.
