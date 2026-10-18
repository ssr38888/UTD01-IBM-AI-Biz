"""ClaimClock advocate screen. Stdlib only (runs on the LinuxONE VM as-is).

python c_product/app.py [--port 8000] [--host 0.0.0.0]
"""
import argparse
import csv
import hashlib
import json
import random
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ARTIFACTS = ROOT / "b_platform" / "artifacts"
SCORES = ARTIFACTS / "scores.csv"
CLAIM_RISK = ARTIFACTS / "claim_risk.csv"
FIXTURE = HERE / "fixtures" / "scores_sample.csv"

sys.path.insert(0, str(HERE))
try:
    import letter  # written in parallel by the lead: stream_letter(row, lang), check_letter(text, row, lang)
except ImportError:
    letter = None

KEEP = ("claim_id", "assembly_date", "score", "rank_pct", "top1", "top2", "top3", "lead_days_est",
        "county", "carrier_type", "industry_code", "cause_code", "nature_code", "body_part_code",
        "days_accident_to_assembly")
# y is loaded for letter.check_letter parity but never sent to the UI.

WCIO = json.loads((HERE / "wcio_codes.json").read_text(encoding="utf-8"))  # {industry,cause,nature,body_part}: {code: text}
FALL_CAUSES = {"25", "26", "27", "28", "29", "31", "32", "33"}

FIRST = ["Maria", "James", "Wei", "Aisha", "Luis", "Priya", "Jamal", "Elena", "Dmitri", "Grace", "Carlos", "Mei",
         "Samuel", "Fatima", "Rosa", "Kwame", "Anna", "Jorge", "Nia", "Thomas"]
LAST = ["Rivera", "Chen", "Okafor", "Patel", "Nowak", "Santos", "Kim", "Johnson", "Hernandez", "Ali", "Murphy", "Diaz",
        "Singh", "Brown", "Lopez", "Cohen", "Nguyen", "Walker", "Garcia", "Russo"]
OCCUPATION = {"62": ["Home health aide", "Nurse", "Patient care technician"], "92": ["Sanitation worker", "Clerk", "Corrections officer"],
              "61": ["Teacher", "School custodian", "Bus aide"], "44": ["Store associate", "Stock clerk"],
              "45": ["Store associate", "Cashier"], "48": ["Delivery driver", "Bus operator"], "49": ["Warehouse picker", "Package handler"],
              "72": ["Line cook", "Server", "Housekeeper"], "23": ["Laborer", "Carpenter", "Electrician"],
              "56": ["Security guard", "Janitor"], "71": ["Groundskeeper", "Stagehand"], "33": ["Machine operator", "Assembler"],
              "81": ["Mechanic", "Hairdresser"], "42": ["Forklift operator", "Sales representative"]}


def profile(row, a=None):
    """Deterministic synthetic submitter: the public data has no personal fields. Labeled as such in the UI.
    a: claim-risk analytics row (its prior_* and fall_video are simulated too)."""
    seed = int(hashlib.md5(str(row["claim_id"]).encode()).hexdigest(), 16)
    rng = random.Random(seed)
    ind = row.get("industry_code") or ""
    if a:
        prior = f"{a['prior_claims_5y']} claim{'s' * (a['prior_claims_5y'] != 1)}, {a['prior_disputes_5y']} disputed (simulated)" if a["prior_claims_5y"] else "None on file (simulated)"
    return {
        "synthetic": True,
        "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
        "age_band": rng.choice(["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]),
        "occupation": rng.choice(OCCUPATION.get(ind, ["Worker"])),
        "language": rng.choice(["en", "en", "en", "es"]),
        "contact": rng.choice(["Mail", "Phone", "Text message", "Email"]),
        "prior_claims": prior if a else "None on file",
        # Teammate's fall-detection model is separate; the card is a placeholder shown for some fall claims.
        "fall_video": bool(a["fall_video"]) if a else row.get("cause_code") in FALL_CAUSES and seed % 3 == 0,
    }


def words(row):
    def days_ago(n):
        try:
            return (date.fromisoformat(row["assembly_date"]) - timedelta(days=round(float(n)))).isoformat()
        except (ValueError, TypeError):
            return None
    code = lambda table, k: WCIO[table].get(row.get(k) or "", f"code {row.get(k) or '?'}")
    return {
        "industry": code("industry", "industry_code"),
        "cause": code("cause", "cause_code"),
        "nature": code("nature", "nature_code"),
        "body_part": code("body_part", "body_part_code"),
        "accident_date": days_ago(row.get("days_accident_to_assembly")),
    }


def load_rows():
    path = SCORES if SCORES.exists() else FIXTURE
    rows, days = [], {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                row = {k: r.get(k, "") for k in KEEP}
                row["score"] = float(r["score"])
                row["rank_pct"] = float(r["rank_pct"])
                row["flagged"] = row["rank_pct"] >= 90
                row["y"] = r.get("y", "")
            except (ValueError, KeyError):
                continue  # malformed row: skip rather than crash the demo
            rows.append(row)
            d = days.setdefault(row["assembly_date"], {"date": row["assembly_date"], "claims": 0, "flagged": 0})
            d["claims"] += 1
            d["flagged"] += row["flagged"]
    print(f"loaded {len(rows)} claims from {path}", file=sys.stderr)
    return rows, sorted(days.values(), key=lambda d: d["date"], reverse=True)


def load_analytics():
    """claim_risk.csv has ~270k rows; keep the raw line per id (~30 MB) and parse on request."""
    if not CLAIM_RISK.exists():
        return [], {}
    with open(CLAIM_RISK, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        lines = {ln.split(",", 1)[0]: ln.strip() for ln in f if ln.strip()}
    print(f"loaded claim-risk analytics for {len(lines)} claims", file=sys.stderr)
    return header, lines


RISK_HEADER, RISK_LINES = load_analytics()
RISK_INT = {"prior_claims_5y", "prior_disputes_5y", "fall_video"}


def analytics(claim_id):
    ln = RISK_LINES.get(claim_id)
    if ln is None:
        return None
    out = {}
    for k, v in zip(RISK_HEADER, ln.split(",")):
        if k in ("claim_id", "fall_intensity"):
            out[k] = v
        elif v == "":
            out[k] = None
        else:
            out[k] = int(float(v)) if k in RISK_INT else float(v)
    return out


ROWS, DAYS = load_rows()
BY_ID = {r["claim_id"]: r for r in ROWS}
BY_DAY = {}
for _r in ROWS:
    BY_DAY.setdefault(_r["assembly_date"], []).append(_r)
for _v in BY_DAY.values():
    _v.sort(key=lambda r: -r["score"])


def public(row):
    return {k: v for k, v in row.items() if k != "y"}


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # chunked streaming needs 1.1; every other response sets Content-Length

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def row_or_404(self, q):
        row = BY_ID.get((q.get("id") or [""])[0])
        if row is None:
            self.send_json({"error": "unknown claim id"}, 404)
        return row

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            body = (HERE / "static" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/days":
            self.send_json(DAYS)
        elif u.path == "/api/queue":
            day = (q.get("date") or [DAYS[0]["date"] if DAYS else ""])[0]
            rows = BY_DAY.get(day, [])
            self.send_json({"date": day, "claims": len(rows), "flagged": sum(r["flagged"] for r in rows),
                            "rows": [public(r) for r in rows]})
        elif u.path == "/api/claim":
            row = self.row_or_404(q)
            if row:
                a = analytics(row["claim_id"])
                self.send_json({**public(row), "words": words(row), "submitter": profile(row, a), "analytics": a})
        elif u.path == "/api/letter":
            row = self.row_or_404(q)
            if not row:
                return
            lang = (q.get("lang") or ["en"])[0]
            if letter is None:
                self.send_json({"error": "letter module not available"}, 503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for chunk in letter.stream_letter(public(row), lang):
                    data = chunk.encode("utf-8")
                    if data:
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                        self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif u.path == "/api/meta":
            meta = read_json(ARTIFACTS / "model.json") or {}
            bench = read_json(ARTIFACTS / "benchmark.json")
            if bench:
                meta["benchmark"] = bench
            cr = read_json(ARTIFACTS / "claim_risk_metrics.json")
            if cr:
                for m in cr.get("models", {}).values():   # deciles are bulky and unused by the UI
                    for split in m.values():
                        if isinstance(split, dict) and isinstance(split.get("calibration"), dict):
                            split["calibration"].pop("deciles", None)
                meta["claim_risk"] = cr
            self.send_json(meta)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/check":
            self.send_json({"error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            row = BY_ID[str(body["id"])]
            text, lang = str(body.get("text", "")), str(body.get("lang", "en"))
        except (ValueError, KeyError):
            self.send_json({"error": "bad request: need id, text, lang"}, 400)
            return
        if letter is None:
            self.send_json({"error": "letter module not available"}, 503)
            return
        problems = list(letter.check_letter(text, public(row), lang))
        self.send_json({"ok": not problems, "problems": problems})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="0.0.0.0")
    a = p.parse_args()
    print(f"ClaimClock on http://{a.host}:{a.port}  letter module: {'ok' if letter else 'MISSING'}", file=sys.stderr)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
