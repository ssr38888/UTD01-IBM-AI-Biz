# Pull the extra day-0 fields for 2023–2025
Label: `wayfinder:task` · type AFK · blocks: 03

## Question

Download the fields in ticket 01 for every 2023–2025 claim from data.ny.gov and join them on claim id (`a_data/fetch_extra.py` → `data/modeling/extra_fields.csv.gz`).

## Resolution (closed)

Pulled 810,613 claims (2023–2025) into `data/modeling/extra_fields.csv.gz` (11 MB). `c3_date` is empty for 76% of claims, `c2_date` for 2%.
