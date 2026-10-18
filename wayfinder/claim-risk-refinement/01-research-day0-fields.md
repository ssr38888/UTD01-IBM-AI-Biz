# Are the extra public fields known when a claim is assembled?
Label: `wayfinder:research` · type AFK · blocks: 03

## Question

In NY WCB's "Assembled Workers' Compensation Claims" dataset, are `c2_date`, `c3_date`, `age_at_injury`, `gender`, `carrier_name`, `zip_code`, `district_name`, `medical_fee_region`, `accident_ind`, `occupational_disease_ind`, `covid_19_indicator` and `alternative_dispute_resolution` values that exist at assembly, or can they be filled/revised later (leakage)? Primary sources only (data.ny.gov data dictionary / WCB docs).

## Resolution (closed)

Dataset is a quarterly-updated snapshot. `accident_ind` and `occupational_disease_ind` are WCB determinations made after assembly: excluded. `c2_date`/`c3_date` are used only as "received on or before assembly" flags and lead days. Carrier, district, COVID and ADR flags are usable with a snapshot caveat. Detail: [findings](01-research-day0-fields.findings.md).
