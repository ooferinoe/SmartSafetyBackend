import os
import json
import argparse
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
violations_ref = db.collection("violations")


def normalize_confidence(v):
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v * 100)) if 0.0 <= v <= 1.0 else int(round(v))
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return int(s)
        try:
            f = float(s)
            return int(round(f * 100)) if 0.0 <= f <= 1.0 else int(round(f))
        except Exception:
            return None
    return None


def migrate(limit=500, dry_run=True):
    docs = list(violations_ref.limit(limit).stream())
    print(f"Migrating up to {len(docs)} violation docs (limit={limit}) dry_run={dry_run}")
    for d in docs:
        data = d.to_dict() or {}
        updates = {}
        # confidence
        if "confidence" in data:
            new_conf = normalize_confidence(data["confidence"])
            if new_conf is not None and new_conf != data["confidence"]:
                updates["confidence"] = new_conf
        # bbox
        if "bbox" not in data or not isinstance(data.get("bbox"), (list, tuple)):
            updates.setdefault("bbox", [])
        # violationId
        if not data.get("violationId"):
            updates["violationId"] = d.id
        # ensure alertSent boolean
        if "alertSent" not in data:
            updates["alertSent"] = bool(data.get("alertSentTo"))
        # ensure alertSentTo is a list
        ats = data.get("alertSentTo")
        if ats is None:
            updates.setdefault("alertSentTo", [])
        elif not isinstance(ats, (list, tuple)):
            updates["alertSentTo"] = [ats]

        if updates:
            print(f"{'DRY' if dry_run else 'APPLY'} {d.id}: {updates}")
            if not dry_run:
                violations_ref.document(d.id).set(updates, merge=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate violations collection")
    p.add_argument("--limit", "-n", type=int, default=500, help="Number of docs to process")
    p.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = p.parse_args()
    migrate(limit=args.limit, dry_run=not args.apply)