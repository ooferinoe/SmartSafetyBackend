import os
from dotenv import load_dotenv

load_dotenv()

MODEL_SERVICE_URL = os.getenv("MODEL_SERVICE_URL", "https://ooferinoe-smart-safety-model.hf.space")
STREAM_URL = os.getenv("STREAM_URL")
CAMERA_ID = os.getenv("CAMERA_ID", "CAM 001")

FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "serviceAccountKey.json")

BREVO_USER = os.getenv("BREVO_USER")
BREVO_PASS= os.getenv("BREVO_PASS")
BREVO_SENDER= os.getenv("BREVO_SENDER")

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")

CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET')


API_KEY_MODEL = os.getenv("API_KEY_MODEL")
TZ = os.getenv("TZ", "Asia/Manila")

# comment