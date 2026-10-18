"""Trained tabular dispute scoring linked to captured FallWatch incidents."""
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy.orm import Session

from . import config
from .db import ClaimAssessment, Event
from .intensity import classify_intensity

FEATURES = ["industry_code", "wcio_cause_of_injury_code", "wcio_nature_of_injury_code",
            "wcio_part_of_body_code", "county_of_injury", "carrier_type",
            "days_accident_to_assembly", "assembly_month"]


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    claim_reference: StrictStr = Field(min_length=1, max_length=100)
    industry_code: StrictStr | None = Field(max_length=100)
    wcio_cause_of_injury_code: StrictStr | None = Field(max_length=100)
    wcio_nature_of_injury_code: StrictStr | None = Field(max_length=100)
    wcio_part_of_body_code: StrictStr | None = Field(max_length=100)
    county_of_injury: StrictStr | None = Field(max_length=100)
    carrier_type: StrictStr | None = Field(max_length=100)
    days_accident_to_assembly: float | None = Field(ge=0, le=100000, allow_inf_nan=False)
    assembly_month: int = Field(ge=1, le=12, strict=True)


@lru_cache(maxsize=1)
def model_bundle():
    # Only load the locally trained, trusted artifact installed by the operator.
    path = Path(os.environ.get("CLAIMCLOCK_MODEL_PATH", config.ROOT / "backend/models/model.joblib"))
    if not path.is_file():
        raise HTTPException(503, "ClaimClock trained model is not installed")
    try:
        import joblib
        bundle = joblib.load(path)
    except Exception as exc:
        raise HTTPException(503, "ClaimClock model could not be loaded") from exc
    if bundle.get("features") != FEATURES:
        raise HTTPException(503, "ClaimClock model input contract does not match")
    return bundle, hashlib.sha256(path.read_bytes()).hexdigest()


def score_claim(fields):
    import pandas as pd
    bundle, digest = model_bundle()
    frame = pd.DataFrame([{name: fields[name] for name in FEATURES}])
    for name in FEATURES[:6]:
        frame[name] = frame[name].fillna("MISSING")
    for name in FEATURES[6:]:
        frame[name] = pd.to_numeric(frame[name]).astype(float)
    probability = float(bundle["model"].predict_proba(frame[FEATURES])[:, 1][0])
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise HTTPException(503, "ClaimClock model returned an invalid score")
    return probability, digest


def population_baseline():
    path = Path(os.environ.get("CLAIMCLOCK_POPULATION_PATH", config.ROOT / "backend/models/population_baseline.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        count, disputes = data["training_claims"], data["recorded_disputes"]
        probability = data["probability"]
        if not (isinstance(count, int) and isinstance(disputes, int) and count > 0
                and 0 <= disputes <= count and math.isfinite(probability)
                and math.isclose(probability, disputes / count, rel_tol=1e-12)):
            raise ValueError("Invalid population baseline")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, "Historical baseline is not available") from exc


def evidence_integrity(event, db, verify):
    try:
        intact = bool(verify(event, db)["ok"])
    except (ValueError, KeyError, TypeError, OSError):
        intact = False
    return intact, intact and {"standing", "laid"} <= {image.role for image in event.images}


def register_claim_routes(app, viewer_auth, get_db, same_origin, verify, storage):
    @app.get("/api/events/{event_id}/dispute-score")
    def automatic_score(event_id: str, _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
        event = db.get(Event, event_id)
        if event is None:
            raise HTTPException(404, "Event not found")
        baseline = population_baseline()
        intact, eligible = evidence_integrity(event, db, verify)
        probability = baseline["probability"]
        intensity = classify_intensity(None, None)
        if eligible:
            try:
                manifest = json.loads(storage().get(f"events/{event_id}/manifest.json"))
                poses = json.loads(storage().get(f"events/{event_id}/keypoints.json"))
                if manifest.get("keypoints_sha256"):
                    intensity = classify_intensity(manifest.get("descriptor"), poses)
            except (ValueError, KeyError, TypeError, OSError):
                pass
        reduction = intensity["assumed_relative_reduction"]
        photo_eligible = intact and bool(event.images)
        score_basis = "measured_motion_policy" if reduction is not None else "no_verified_evidence"
        reduction_range = probability_range = None
        if reduction is None and photo_eligible:
            # Scenario boundaries, not a random point estimate or statistical confidence interval.
            reduction_range = {"min": 0, "max": .10}
            probability_range = {"min": probability * .9, "max": probability}
            score_basis = "verified_photos_policy"
        if reduction is not None:
            lower, upper = {"low": (0, .10), "medium": (.10, .25), "high": (.25, .35)}[intensity["level"]]
            reduction_range = {"min": lower, "max": upper}
            probability_range = {"min": probability * (1 - upper), "max": probability * (1 - lower)}
        return {"event_id": event_id, "baseline_probability": probability,
                "baseline_basis": "historical_population_rate", "population": baseline,
                "adjusted_probability": None,
                "adjustment_basis": "assumption_based", "assumed_relative_reduction": None,
                "assumed_relative_reduction_range": reduction_range,
                "adjusted_probability_range": probability_range,
                "score_basis": score_basis,
                "intensity": intensity,
                "target": "recorded_dispute_within_90_days", "evidence_verified": intact,
                "evidence_eligible": photo_eligible, "motion_evidence_eligible": eligible, "evidence_photos": len(event.images),
                "manifest_sha256": event.manifest_sha256}

    @app.get("/api/events/{event_id}/claim")
    def read_claim(event_id: str, _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
        if db.get(Event, event_id) is None:
            raise HTTPException(404, "Event not found")
        saved = db.get(ClaimAssessment, event_id)
        return {"assessment": json.loads(saved.assessment_json) if saved else None}

    @app.post("/api/events/{event_id}/claim")
    def assess_claim(event_id: str, fields: ClaimInput, request: Request,
                     _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
        if not same_origin(request):
            raise HTTPException(403, "same-origin claim request required")
        event = db.get(Event, event_id)
        if event is None:
            raise HTTPException(404, "Event not found")
        inputs = fields.model_dump()
        baseline, model_sha = score_claim(inputs)
        try:
            intact = verify(event, db)["ok"]
        except (ValueError, KeyError, TypeError, OSError):
            # Malformed evidence cannot earn an adjustment or block baseline scoring.
            intact = False
        roles = {image.role for image in event.images}
        eligible = intact and {"standing", "laid"} <= roles
        # Explicit policy assumption, not a photo-trained prediction or intensity measurement.
        reduction = .25 if eligible else 0
        result = {
            "event_id": event_id, "inputs": inputs,
            "baseline_probability": baseline,
            "adjusted_probability": baseline * (1 - reduction) if eligible else None,
            "adjustment_basis": "assumption_based", "assumed_relative_reduction": reduction,
            "model_sha256": model_sha, "target": "recorded_dispute_within_90_days",
            "evidence_verified": intact, "evidence_eligible": eligible,
            "evidence_photos": len(event.images), "manifest_sha256": event.manifest_sha256,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }
        saved = db.get(ClaimAssessment, event_id)
        if saved is None:
            saved = ClaimAssessment(event_id=event_id, assessment_json=json.dumps(result))
            db.add(saved)
        else:
            saved.assessment_json = json.dumps(result)
        db.commit()
        return result
