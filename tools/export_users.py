import os
import json
from datetime import datetime, timezone
from firebase_admin import credentials, firestore, initialize_app

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
    # Recursively convert datetimes to ISO strings and DocumentRefs to path strings
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    if isinstance(obj, datetime):
        dt = obj if obj.tzinfo else obj.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    try:
        # DocumentReference -> path string (avoid importing type)
        if hasattr(obj, "path") and hasattr(obj, "id"):
            return str(obj.path)
    except Exception:
        pass
    return obj

out = {}
for d in db.collection("users").stream():
    out[d.id] = normalize(d.to_dict() or {})

with open("users_export.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("Exported", len(out), "users -> users_export.json")