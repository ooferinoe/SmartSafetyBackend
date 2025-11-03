import os, json
from datetime import datetime, timezone
from firebase_admin import credentials, firestore, initialize_app

FIREBASE_CRED = os.getenv("FIREBASE_CRED", "")
if not FIREBASE_CRED:
    raise SystemExit("Set FIREBASE_CRED env var to service account path or JSON text")

if os.path.exists(FIREBASE_CRED):
    cred = credentials.Certificate(FIREBASE_CRED)
else:
    cred = credentials.Certificate(json.loads(FIREBASE_CRED))

initialize_app(cred)
db = firestore.client()

def normalize(o):
    if isinstance(o, dict):
        return {k: normalize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [normalize(v) for v in o]
    if hasattr(o, "isoformat") and getattr(o, "tzinfo", None) is not None:
        return o.isoformat()
    try:
        if hasattr(o, "path"):
            return str(o.path)
    except Exception:
        pass
    return o

out = {}
for d in db.collection("alerts").stream():
    out[d.id] = normalize(d.to_dict() or {})

with open("alerts_export.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("Exported", len(out), "alerts -> alerts_export.json")