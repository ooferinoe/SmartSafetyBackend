import requests
import cv2
from io import BytesIO
from config import API_KEY_MODEL

def predict_frame_via_service(model_url, frame_bgr, timeout=30, jpeg_quality=85):
    # encode frame to JPG
    ok, buf = cv2.imencode('.jpg', frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not ok:
        raise RuntimeError("failed to encode frame")
    files = {'file': ('frame.jpg', buf.tobytes(), 'image/jpeg')}
    headers = {}
    if API_KEY_MODEL:
        headers['Authorization'] = f"Bearer {API_KEY_MODEL}"
    r = requests.post(f"{model_url.rstrip('/')}/predict", files=files, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()