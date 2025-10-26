from firebase_admin import credentials, firestore, initialize_app
from config import FIREBASE_CRED
import os
import json

# initialize once
# FIREBASE_CRED can be a path to a json file or the JSON text itself (useful for Railway secrets)
if os.path.exists(FIREBASE_CRED):
    cred = credentials.Certificate(FIREBASE_CRED)
else:
    # assume FIREBASE_CRED contains JSON text
    cred_dict = json.loads(FIREBASE_CRED)
    cred = credentials.Certificate(cred_dict)

initialize_app(cred)
db = firestore.client()
violations_ref = db.collection("violations")

def add_violation(doc: dict):
    return violations_ref.add(doc)

def query_violations_by_timestamp(ts_iso: str):
    return violations_ref.where("timestamp", "==", ts_iso).get()