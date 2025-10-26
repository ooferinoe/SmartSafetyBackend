import os
from dotenv import load_dotenv

load_dotenv()

MODEL_SERVICE_URL = os.getenv("MODEL_SERVICE_URL", "http://localhost:8000")
STREAM_URL = os.getenv("STREAM_URL", "http://192.168.1.11:8080/video")
CAMERA_ID = os.getenv("CAMERA_ID", "CAM 001")

FIREBASE_CRED = os.getenv("FIREBASE_CRED", "serviceAccountKey.json")

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")

API_KEY_MODEL = os.getenv("API_KEY_MODEL")
TZ = os.getenv("TZ", "Asia/Manila")

# comment