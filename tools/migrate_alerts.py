import os, json, argparse
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
alerts_ref = db.collection("alerts")
users_ref = db.collection("users")

def migrate(limit=500, dry_run=True, remove_userId=False, report_path="alerts_migration_report.json"):
    docs = list(alerts_ref.limit(limit).stream())
    report = {"total": len(docs), "to_set_uid": [], "skipped_no_userId": [], "unresolved": [], "errors": []}

    for d in docs:
        docid = d.id
        data = d.to_dict() or {}
        legacy = data.get("userId")
        if not legacy:
            report["skipped_no_userId"].append(docid)
            continue

        resolved_uid = None
        try:
            # Prefer users collection lookup (document id may already be uid)
            udoc = users_ref.document(legacy).get()
            if udoc.exists:
                udata = udoc.to_dict() or {}
                resolved_uid = udata.get("uid") or udoc.id
            else:
                # try resolving via Auth (legacy may already be a real auth uid)
                try:
                    a = auth.get_user(legacy)
                    resolved_uid = a.uid
                except auth.UserNotFoundError:
                    # fallback: try email in alert (first item of alertSentTo if list)
                    email = data.get("alertSentTo") or data.get("email")
                    if isinstance(email, list) and email:
                        email = email[0]
                    if email:
                        try:
                            a2 = auth.get_user_by_email(email)
                            resolved_uid = a2.uid
                        except auth.UserNotFoundError:
                            resolved_uid = None
        except Exception as e:
            report["errors"].append({"doc": docid, "error": str(e)})
            continue

        if not resolved_uid:
            report["unresolved"].append({"doc": docid, "userId": legacy})
            continue

        report["to_set_uid"].append({"doc": docid, "resolved_uid": resolved_uid})
        if not dry_run:
            try:
                alerts_ref.document(docid).update({"uid": resolved_uid})
                if remove_userId:
                    alerts_ref.document(docid).update({"userId": firestore.DELETE_FIELD})
            except Exception as e:
                report["errors"].append({"doc": docid, "error": str(e)})

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("Report written to", report_path)
    print("Summary:", {k: len(v) if isinstance(v, list) else v for k, v in report.items() if k != "total"})

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate alerts userId -> uid (dry-run default)")
    p.add_argument("--limit", "-n", type=int, default=500)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--remove-userid", action="store_true")
    p.add_argument("--report", type=str, default="alerts_migration_report.json")
    args = p.parse_args()
    migrate(limit=args.limit, dry_run=not args.apply, remove_userId=args.remove_userid, report_path=args.report)