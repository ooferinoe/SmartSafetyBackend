from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import cv2
from config import STREAM_URL, CAMERA_ID, MODEL_SERVICE_URL
from services.model_client import predict_frame_via_service
from services.storage import add_violation, query_violations_by_timestamp
from services.emailer import send_alert
import datetime
from zoneinfo import ZoneInfo
import requests
import numpy as np

router = APIRouter()

@router.get("/get_frame_detections")
def get_frame_detections():
    # fetch a single JPEG snapshot from STREAM_URL (preferred for cloud hosts)
    try:
        r = requests.get(STREAM_URL, timeout=5)
        r.raise_for_status()
        jpg = np.frombuffer(r.content, dtype=np.uint8)
        frame = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
        if frame is None:
            return JSONResponse({"error": "failed to decode frame"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch frame: {e}"}, status_code=500)

    # proxy to model-service
    resp = predict_frame_via_service(MODEL_SERVICE_URL, frame)
    return JSONResponse(resp)

@router.get("/detect_ipcam")
def detect_ipcam():
    # similar to previous get_frame_detections but persists to firestore using storage helpers
    # implement detection -> filter -> store logic here (use services.storage.add_violation)
    return JSONResponse({"message": "implement detect_ipcam logic"})

@router.post("/send_alert_email")
async def post_send_alert_email(request: Request):
    data = await request.json()
    to = data.get("to_email") or data.get("alertSentTo")
    violation_type = data.get("violationType") or data.get("violation_type")
    confidence = data.get("confidence")
    timestamp = data.get("timestamp") or data.get("date_time")
    try:
        send_alert(to, violation_type, confidence, timestamp)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/health")
def health_check():
    return JSONResponse({"status": "healthy"})