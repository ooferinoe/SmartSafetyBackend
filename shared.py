from firebase_admin import credentials, firestore, initialize_app
import cloudinary
import cloudinary.uploader

# --- Secure Configuration ---
cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME')
api_key = os.environ.get('CLOUDINARY_API_KEY')
api_secret = os.environ.get('CLOUDINARY_API_SECRET')
if not all([cloud_name, api_key, api_secret]):
    print("FATAL ERROR: Cloudinary credentials missing."); sys.exit(1)
cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASS = os.environ.get("GMAIL_PASS")
if not all([GMAIL_USER, GMAIL_PASS]):
    print("FATAL ERROR: Gmail credentials missing."); sys.exit(1)

# --- Model and DB Setup ---
YOLOV5_MODEL_PATH = r"C:\YOLOv5\yolov5\runs\train\exp\weights\best.pt"
model = torch.hub.load('ultralytics/yolov5', 'custom', path=YOLOV5_MODEL_PATH, source='local', force_reload=True)
cred = credentials.Certificate("serviceAccountKey.json")
initialize_app(cred)
db = firestore.client()
violations_ref = db.collection("violations")
STREAM_URL = "http://192.168.1.6:8080/video"
UNRESOLVED_CLASSES = { 'Improper Hard Hat', 'Improper Safety Glasses', 'Improper Safety Gloves', 'Improper Safety Shoes', 'No Hard Hat', 'No Reflectorized Vest', 'No Safety Glasses', 'No Safety Gloves' }
