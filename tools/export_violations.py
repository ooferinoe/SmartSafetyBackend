import os
import json
from firebase_admin import credentials, firestore, initialize_app
from datetime import datetime, timezone

FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "")
if not FIREBASE_CRED_PATH:
    raise SystemExit("Set FIREBASE_CRED_PATH env var to service account path or JSON text")

if os.path.exists(FIREBASE_CRED_PATH):
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
else:
    cred = credentials.Certificate(json.loads(FIREBASE_CRED_PATH))

initialize_app(cred)
db = firestore.client()

def normalize(obj):
    # recursively convert datetimes to ISO strings and leave other values unchanged
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    if isinstance(obj, datetime):
        # ensure timezone-aware and use ISO format
        dt = obj if obj.tzinfo else obj.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    # DocumentReference -> path string
    try:
        # avoid importing firestore types; check for attribute common to doc refs
        if hasattr(obj, "path") and hasattr(obj, "id"):
            return str(obj.path)
    except Exception:
        pass
    return obj

out = {}
for d in db.collection("violations").stream():
    out[d.id] = normalize(d.to_dict() or {})

with open("violations_export.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("Exported", len(out), "violations -> violations_export.json")