"""Pull extra day-0 fields for 2023-2025 claims from NY Open Data (public, no key).

python a_data/fetch_extra.py  ->  data/modeling/extra_fields.csv.gz (joined on claim_identifier)
Only fields known when the claim is assembled; outcome fields (attorney, hearings, injury type,
wage, status) are deliberately not requested.
"""
import io
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "modeling" / "extra_fields.csv.gz"
FIELDS = ["claim_identifier", "assembly_date", "accident_ind", "age_at_injury", "gender", "c2_date", "c3_date",
          "carrier_name", "district_name", "medical_fee_region", "occupational_disease_ind",
          "covid_19_indicator", "alternative_dispute_resolution", "zip_code"]
PAGE = 50_000


def page(offset):
    q = urllib.parse.urlencode({"$select": ",".join(FIELDS), "$order": "claim_identifier", "$limit": PAGE, "$offset": offset,
                                "$where": "assembly_date between '2023-01-01' and '2025-12-31T23:59:59'"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen("https://data.ny.gov/resource/jshw-gkgu.csv?" + q, timeout=300) as r:
                return pd.read_csv(io.BytesIO(r.read()), dtype=str)
        except OSError:
            time.sleep(2 ** (attempt + 1))
    raise SystemExit(f"failed at offset {offset}")


def main():
    parts, offset = [], 0
    while True:
        part = page(offset)
        parts.append(part)
        print(f"{offset + len(part):,} rows", flush=True)
        if len(part) < PAGE:
            break
        offset += PAGE
    frame = pd.concat(parts, ignore_index=True).drop_duplicates("claim_identifier")
    frame.to_csv(OUT, index=False, compression="gzip")
    print(f"wrote {OUT} ({len(frame):,} claims)")


if __name__ == "__main__":
    main()
