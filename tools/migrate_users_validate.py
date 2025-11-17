import os
import json
import argparse
from firebase_admin import credentials, firestore, initialize_app, auth

FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "")
if not FIREBASE_CRED_PATH:
    raise SystemExit("Set FIREBASE_CRED_PATH env var to service account path or JSON text")

if os.path.exists(FIREBASE_CRED_PATH):
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
else:
    cred = credentials.Certificate(json.loads(FIREBASE_CRED_PATH))

initialize_app(cred)
db = firestore.client()
users_ref = db.collection("users")

def migrate(limit=500, dry_run=True, remove_userId=False, report_path="users_migration_report.json"):
    docs = list(users_ref.limit(limit).stream())
    report = {
        "total": len(docs),
        "to_set_uid": [],
        "skipped_no_userId": [],
        "unresolved_userId": [],
        "conflicts": [],
        "errors": []
    }

    for d in docs:
        docid = d.id
        data = d.to_dict() or {}
        userId = data.get("userId")
        uid_field = data.get("uid")
        email = data.get("email")

        if not userId:
            report["skipped_no_userId"].append(docid)
            continue

        resolved_auth_uid = None
        found_by = None
        try:
            # Try resolving by userId (assumed to be uid)
            try:
                u = auth.get_user(userId)
                resolved_auth_uid = u.uid
                found_by = "uid"
            except auth.UserNotFoundError:
                resolved_auth_uid = None

            # Fallback: try resolving by email for info-only
            if resolved_auth_uid is None and email:
                try:
                    u2 = auth.get_user_by_email(email)
                    resolved_auth_uid = u2.uid
                    found_by = "email"
                except auth.UserNotFoundError:
                    resolved_auth_uid = None
        except Exception as e:
            report["errors"].append({"doc": docid, "error": str(e)})
            continue

        # Decide action
        if resolved_auth_uid is None:
            report["unresolved_userId"].append({"doc": docid, "userId": userId, "email": email})
            continue

        if uid_field is None:
            # Only set uid when resolved_auth_uid == stored userId (safe)
            if resolved_auth_uid == userId:
                report["to_set_uid"].append(docid)
                if not dry_run:
                    updates = {"uid": resolved_auth_uid}
                    try:
                        users_ref.document(docid).update(updates)
                        if remove_userId:
                            users_ref.document(docid).update({"userId": firestore.DELETE_FIELD})
                    except Exception as e:
                        report["errors"].append({"doc": docid, "error": str(e)})
            else:
                report["conflicts"].append({"doc": docid, "userId": userId, "resolved_auth_uid": resolved_auth_uid, "found_by": found_by})
        else:
            if uid_field != resolved_auth_uid:
                report["conflicts"].append({"doc": docid, "userId": userId, "uid_field": uid_field, "resolved_auth_uid": resolved_auth_uid, "found_by": found_by})

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("Report written to", report_path)
    print("Summary:", {k: len(v) if isinstance(v, list) else v for k, v in report.items() if k != "total"})

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Validate userId against Firebase Auth and safe-backfill uid")
    p.add_argument("--limit", "-n", type=int, default=500, help="Number of user docs to process")
    p.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    p.add_argument("--remove-userid", action="store_true", help="Remove legacy userId when applying")
    p.add_argument("--report", type=str, default="users_migration_report.json", help="Report file path")
    args = p.parse_args()
    migrate(limit=args.limit, dry_run=not args.apply, remove_userId=args.remove_userid, report_path=args.report)