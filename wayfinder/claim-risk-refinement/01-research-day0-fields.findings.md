# Findings: which fields are known at claim assembly (day 0)

Dataset: NYS WCB "Assembled Workers' Compensation Claims: Beginning 2000", data.ny.gov `jshw-gkgu`. Accessed 2026-10-18 (rowsUpdatedAt 1790028512, about 2026-09-21).

Sources:
- [M] Dataset metadata and column descriptions: https://data.ny.gov/api/views/jshw-gkgu.json
- [O] Overview PDF (attachment `ad843bd9-...`): https://data.ny.gov/api/views/jshw-gkgu/files/ad843bd9-36a1-4c17-826f-6736a21ec6ba
- [D] Data dictionary PDF (attachment `bc12a745-...`): https://data.ny.gov/api/views/jshw-gkgu/files/bc12a745-4b03-438a-9a9b-419aa23e9c9f (text matches [M])
- [F] WCB All Forms page: https://www.wcb.ny.gov/content/main/forms/AllForms.jsp
- [Q] SoQL counts I ran on https://data.ny.gov/resource/jshw-gkgu.json (2026-10-18)

## Terms (observed)

- **Assembly**: the Board learns of a workplace injury and assigns a case number. Triggers: more than one week lost work, possible permanent disability, carrier/employer dispute, or a C-3 received [M][O]. `assembly_date` = date "first assembled" [D].
- **C-2 (now C-2F)**: Employer's Report of Work-Related Injury/Illness. Since Q1 2022 datasets, `c2_date` = earliest of equivalent filings, incl. eClaims EDI [M]. Employer files within 10 days of injury [F].
- **C-3**: "Employee Claim" form filed by the injured worker [M][F].

## Snapshot vs as-of-assembly (observed + inference)

- Published **quarterly** [O FAQ 2]. Fields are not frozen at assembly: "Claim Injury Type is as of the date the data set was created" [O FAQ 4]; `current_claim_status` is "Present status"; `highest_process` is "to date" [O FAQ 9-10].
- No source says any field is frozen at assembly. Data comes from the Board's EDW, "cleansed and transformed" [O]. Inference: the file is a **current snapshot**, so every field reflects the latest known value, not the assembly-day value.
- Empirical [Q]: of 5,655,563 claims with `assembly_date`, **927,610 have `c2_date` > assembly_date** and **891,291 have `c3_date` > assembly_date** (838,023 even for pre-2025 assemblies). 3,685,691 have null `c3_date`. So form dates are filled after assembly for a large share.

## Per-field table

| field | known at assembly? | source | note |
|---|---|---|---|
| c2_date | no | [M] [Q] | Received date; null = not received *as of snapshot*. ~16% of claims get it after assembly. Also leaks via presence/absence. Use only as `c2_date <= assembly_date` flag. |
| c3_date | no | [M] [O] [Q] | Same issue; a C-3 can itself trigger assembly, but ~16% arrive later. Presence flag leaks. Use only `c3_date <= assembly_date`. |
| age_at_injury | yes (mostly) | [M] [O FAQ 6] | Derived from birth date + accident date; 0 when info missing, so could be filled later. Low risk. |
| gender | unclear (likely yes) | [M] | "Reported gender"; U if not specified. No statement on when set or if updated. |
| carrier_name | unclear | [M] [O FAQ 12] | "Primary insurance provider responsible". Liability can shift (e.g., ATF, Special Funds 25-A for reopened cases, "carrier undetermined"). Snapshot value, may differ from day 0. |
| zip_code | unclear (likely yes) | [M] | Worker's "reported" home ZIP; snapshot, could be updated on address change. |
| district_name | unclear | [M] [O] | Office that "oversees" claims; overview calls it district "where the claim was adjudicated" and groups it with resolution results. "Virtual Only" district implies reassignment possible. |
| medical_fee_region | unclear (likely yes) | [M] | Derived from ZIP mapping; inherits zip_code risk. |
| accident_ind | no / unclear | [M] [O FAQ 7] | "as determined by the WCB"; determination is part of ANCR/ODNCR, which happens after assembly (`interval_assembled_to_ancr`). |
| occupational_disease_ind | no / unclear | [M] [O] | Same: "as determined by the WCB"; ODNCR after assembly. |
| covid_19_indicator | unclear (mostly yes) | [M] | Rule-based on WCIO nature code 83 or injury text; those come from FROI/EDI filings that may arrive or be corrected after assembly. |
| alternative_dispute_resolution | unclear | [M] | "Claims subject to adjudication processes external to the Board"; no timing given. Usually set by collective-bargaining ADR program membership of employer (inference, not sourced); value U exists. |

## What the evidence cannot establish

- No field-level audit trail or change history is published; no per-field "set at" timestamp except the dates themselves.
- For demographics (gender, zip, age) and carrier, sources do not say whether values are overwritten after assembly. Only "snapshot" framing argues they could be.
- Older quarterly snapshots are not offered on the dataset page, so as-of-assembly values cannot be reconstructed from this source.

## Verdict

1. Safe-ish day-0 predictors: age_at_injury, gender, zip_code, medical_fee_region (worker-reported, derived at intake; small snapshot risk).
2. Use with caution (snapshot may reflect later reassignment/determination): carrier_name, district_name, covid_19_indicator, alternative_dispute_resolution.
3. Leaky, exclude or rebuild: c2_date/c3_date (only as "received on or before assembly_date"), accident_ind and occupational_disease_ind ("as determined by the WCB", tied to post-assembly ANCR/ODNCR).
