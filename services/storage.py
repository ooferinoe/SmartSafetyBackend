from firebase_admin import credentials, firestore, initialize_app
from config import FIREBASE_CRED_PATH
import os
from typing import Any, Dict, Optional
from datetime import datetime, timezone, timedelta
from google.cloud.firestore import FieldFilter

# Check if the variable was set at all
if not FIREBASE_CRED_PATH:
    raise ValueError("FIREBASE_CRED_PATH environment variable is not set.")

# Check if the file at that path actually exists
if not os.path.exists(FIREBASE_CRED_PATH):
    raise FileNotFoundError(f"Firebase credential file not found at: {FIREBASE_CRED_PATH}")

# Initialize app from the path. This now works for both local and Render.
cred = credentials.Certificate(FIREBASE_CRED_PATH)
initialize_app(cred)

db = firestore.client()
violations_ref = db.collection("violations")


def _normalize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc)
    # canonical type
    t = out.get("type") or out.get("violationType") or out.get("violation_type")
    if t:
        out["type"] = t
        out["violationType"] = t
        out["violation_type"] = t

    # confidence -> int percent
    c = out.get("confidence")
    if isinstance(c, str):
        try:
            out["confidence"] = int(c)
        except Exception:
            try:
                out["confidence"] = int(round(float(c) * 100))
            except Exception:
                out["confidence"] = None
    elif isinstance(c, float):
        out["confidence"] = int(round(c * 100)) if 0.0 <= c <= 1.0 else int(round(c))
    elif isinstance(c, int):
        out["confidence"] = c
    else:
        out.setdefault("confidence", None)

    # bbox -> ensure list
    bbox = out.get("bbox") or out.get("box") or []
    out["bbox"] = list(bbox) if isinstance(bbox, (list, tuple)) else []

    # camera / footage canonical
    cam = out.get("camera_id") or out.get("footageId") or out.get("footage_id")
    if cam:
        out["camera_id"] = cam
        out["footageId"] = cam
        out["footage_id"] = cam

    # status
    out.setdefault("status", "Unresolved")

    # alert fields normalization
    ats = out.get("alertSentTo") or out.get("alert_sent_to") or out.get("alert_email") or out.get("alertEmail")
    if ats is None:
        out["alertSentTo"] = []
    elif isinstance(ats, (list, tuple)):
        out["alertSentTo"] = list(ats)
    else:
        out["alertSentTo"] = [ats]
    out["alert_email"] = out.get("alert_email", out["alertSentTo"])
    out["alertSent"] = bool(out["alertSentTo"])

    # timestamp: set to UTC datetime for consistent querying if not provided
    if "timestamp" in out and isinstance(out["timestamp"], str):
        # keep ISO string if provided, but also add parsed timestamp for queries
        try:
            parsed = datetime.fromisoformat(out["timestamp"])
            out["_ts"] = parsed.astimezone(timezone.utc)
        except Exception:
            out["_ts"] = datetime.utcnow().replace(tzinfo=timezone.utc)
    else:
        out["_ts"] = datetime.utcnow().replace(tzinfo=timezone.utc)

    return out

def find_recent_similar(camera_id: str, violation_type: str, within_seconds: int = 30) -> Optional[str]:
    """
    Return existing violation doc id if a violation with same camera_id and type exists
    within 'within_seconds' window.
    Requires stored documents to have _ts (UTC datetime) field (normalized on write).
    """
    cutoff = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(seconds=within_seconds)
    q = violations_ref.where(filter=FieldFilter("camera_id", "==", camera_id))\
        .where(filter=FieldFilter("type", "==", violation_type))\
        .where(filter=FieldFilter("_ts", ">=", cutoff))\
        .limit(1)
        
    docs = q.get()
    for d in docs:
        return d.id
    return None

def add_violation(doc: dict, dedupe_window_seconds: int = 30):
    """
    Normalize, dedupe (optional), create document, embed violationId and stored UTC ts.
    Returns document id or existing doc id if deduped.
    """
    normalized = _normalize_doc(doc)
    cam = normalized.get("camera_id")
    vtype = normalized.get("type")
    if cam and vtype and dedupe_window_seconds > 0:
        existing_id = find_recent_similar(cam, vtype, within_seconds=dedupe_window_seconds)
        if existing_id:
            # update alertSent/alertSentTo if needed instead of creating duplicate
            return existing_id

    # create document, embed id and store _ts as Firestore Timestamp for queries
    doc_ref = violations_ref.document()
    normalized["violationId"] = doc_ref.id
    # set both ISO timestamp string and Firestore Timestamp/searchable _ts
    ts_dt = normalized.get("_ts", datetime.utcnow().replace(tzinfo=timezone.utc))
    normalized["timestamp"] = ts_dt.isoformat()
    normalized["_ts"] = firestore.firestore.SERVER_TIMESTAMP if isinstance(ts_dt, datetime) else firestore.firestore.SERVER_TIMESTAMP
    doc_ref.set(normalized)
    return doc_ref.id

def query_violations_by_timestamp(ts_iso: str):
    return violations_ref.where("timestamp", "==", ts_iso).get()

def increment_daily_scans(count: int):
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    stats_ref = db.collection("stats").document(today_str)

    stats_ref.set({
        "total_scans": firestore.Increment(count), "date": today_str
    }, merge=True)