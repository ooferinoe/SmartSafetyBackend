import os, json, sys
from typing import Any, Dict
from firebase_admin import credentials, firestore, initialize_app
import datetime

FIREBASE_CRED = os.getenv("FIREBASE_CRED", "")
if not FIREBASE_CRED:
    print("Set FIREBASE_CRED env var to service account path or JSON text"); sys.exit(1)

if os.path.exists(FIREBASE_CRED):
    cred = credentials.Certificate(FIREBASE_CRED)
else:
    cred = credentials.Certificate(json.loads(FIREBASE_CRED))

initialize_app(cred)
db = firestore.client()

SCHEMAS = {
    "users": {
        "uid": (str,),
        "email": (str,),
        "firstName": (str,),
        "lastName": (str,),
        "createdAt": (datetime.datetime,),
    },
    "alerts": {
        "uid": (str,),
        "email": (str,),
        "firstName": (str,),
        "lastName": (str,),
        "createdAt": (datetime.datetime,),
    },
    "violations": {
        "violationId": (str,),
        "violationType": (str,),
        "confidence": (int, float, str),
        "bbox": (list,),
        "timestamp": (str, datetime.datetime),
        "date": (str,),
        "footageId": (str,),
        "status": (str,),
        "alertSent": (bool,),
        "alertSentTo": (list,),
    },
}

def inspect_collection(col: str, schema: Dict[str, Any], limit: int = 3):
    docs = list(db.collection(col).limit(limit).stream())
    if not docs:
        print(f"\n{col}: NO DOCUMENTS")
        return
    for d in docs:
        data = d.to_dict()
        print(f"\n=== {col} / {d.id} ===")
        for key, expected in schema.items():
            if key not in data:
                print(f" MISSING: {key}")
                continue
            val = data[key]
            ok = isinstance(val, expected)
            note = ""
            if key == "confidence" and isinstance(val, str) and val.isdigit():
                note = " (string digits)"
            print(f" {key}: {type(val).__name__} => {repr(val)} | expected: {[t.__name__ for t in expected]} | ok: {ok}{note}")
        extras = set(data.keys()) - set(schema.keys())
        if extras:
            print(" Extra keys:", ", ".join(sorted(extras)))

if __name__ == "__main__":
    for col, schema in SCHEMAS.items():
        inspect_collection(col, schema, limit=3)