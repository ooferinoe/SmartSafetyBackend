import logging, time, threading, cv2, requests, numpy as np
from typing import Optional, Tuple, Dict, Any
from fastapi import APIRouter, Request, BackgroundTasks, Depends, Response, Query, UploadFile, File
from fastapi.responses import JSONResponse
from config import STREAM_URL, CAMERA_ID, MODEL_SERVICE_URL
from services.model_client import predict_frame_via_service
from services.processor import process_frame_from_model_response
from services.storage import add_violation, query_violations_by_timestamp
from services.emailer import send_alert
from pydantic import BaseModel
from fastapi import HTTPException
from firebase_admin import firestore
from datetime import datetime, timezone

logger = logging.getLogger("routes")
router = APIRouter()

# simple in-memory cache for last model response (thread-safe)
_model_cache = {"resp": None, "ts": 0.0}
_model_cache_lock = threading.Lock()
_MODEL_TTL = 2.0  # seconds, adjust as needed


def _get_frame_and_run_model() -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    """
    Fetch frame and run model ONCE, but reuse cached model_resp for _MODEL_TTL seconds.
    Returns (model_response, None) on success, or (None, error_response) on failure.
    """
    now = time.time()
    with _model_cache_lock:
        if _model_cache["resp"] is not None and (now - _model_cache["ts"]) <= _MODEL_TTL:
            return _model_cache["resp"], None

    # fetch frame + call model (only when cache expired)
    try:
        r = requests.get(STREAM_URL, timeout=5)
        r.raise_for_status()
        jpg = np.frombuffer(r.content, dtype=np.uint8)
        frame = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
        if frame is None:
            raise Exception("failed to decode frame")
    except Exception as e:
        logger.exception("Helper: failed to fetch frame")
        return None, JSONResponse({"error": f"Failed to fetch frame: {e}"}, status_code=500)

    try:
        model_resp = predict_frame_via_service(MODEL_SERVICE_URL, frame)
    except Exception as e:
        logger.exception("Helper: model call failed")
        return None, JSONResponse({"error": f"Model call failed: {e}"}, status_code=500)

    # store in cache
    with _model_cache_lock:
        _model_cache["resp"] = model_resp
        _model_cache["ts"] = time.time()

    return model_resp, None


# FastAPI dependency wrapper that returns cached model response or raises error
async def get_model_response():
    model_resp, error = _get_frame_and_run_model()
    if error:
        # propagate the JSONResponse as exception (FastAPI will handle it)
        raise error
    return model_resp


ModelResponse = Depends(get_model_response)


# @router.get("/get_frame_detections")
# def get_frame_detections(model_resp: dict = ModelResponse):
#     """
#     Returns the raw model response.
#     The 'model_resp' is provided by the cached dependency.
#     """
#     return JSONResponse(model_resp)


@router.post("/detect")
async def detect_ppe_violation(file: UploadFile = File(...)):
    image_bytes = await file.read()
    jpg = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
    if frame is None:
        return {"error": "Failed to decode image"}
    result = predict_frame_via_service(MODEL_SERVICE_URL, frame)
    global latest_webcam_detection
    latest_webcam_detection = result
    return result

# , model_resp: dict = ModelResponse ------------v
@router.get("/detect_ipcam")
def detect_ipcam(background_tasks: BackgroundTasks):
    """
    Processes the cached model response to find and log violations.
    Processor handles normalization, dedupe and enqueue alerts via background_tasks.
    """
    try:
        result = process_frame_from_model_response(latest_webcam_detection, background_tasks=background_tasks)
        violations = result.get("violations", [])
        # log unresolved violations only
        for v in violations:
            logger.info(
                "Unresolved violation: type=%s confidence=%s id=%s camera=%s",
                v.get("violationType") or v.get("type"),
                v.get("confidence"),
                v.get("violationId"),
                v.get("footageId") or v.get("camera_id", CAMERA_ID),
            )
        return JSONResponse({
            "violations_stored": result.get("violations_stored", 0),
            "unresolved": [v.get("violationType") or v.get("type") for v in violations]
        })
    except JSONResponse as jr:
        # propagated error from dependency
        raise jr
    except Exception as e:
        logger.exception("detect_ipcam: processing failed")
        return JSONResponse({"error": f"processing failed: {e}"}, status_code=500)


@router.post("/send_alert_email")
async def post_send_alert_email(request: Request):
    data = await request.json()
    to = data.get("to_email") or data.get("alertSentTo") or data.get("to")
    violation_type = data.get("violationType") or data.get("violation_type") or data.get("type")
    confidence = data.get("confidence")
    timestamp = data.get("timestamp") or data.get("date_time")
    try:
        send_alert(to, violation_type, confidence, timestamp)
        return JSONResponse({"success": True})
    except Exception as e:
        logger.exception("send_alert_email failed")
        return JSONResponse({"error": f"send_alert failed: {e}"}, status_code=500)
    
@router.get("/detect_ppe")
def detect_ppe(STREAM_URL: str = Query(...)):
    """
    Captures a frame from the IP camera, sends it to the model API, and returns detection results.
    """
    cap = cv2.VideoCapture(STREAM_URL)
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return {"error": "Failed to capture frame from IP camera."}
    result = predict_frame_via_service(MODEL_SERVICE_URL, frame)
    return result

@router.get("/health")
def health_check():
    return JSONResponse({"status": "healthy"})

# --- Alert status update route ---
# Assumes Firestore client 'db' is initialized elsewhere in your codebase
class StatusUpdate(BaseModel):
    status: str
    remarks: str = None

@router.patch("/alerts/{alert_id}/status")
async def update_alert_status(alert_id: str, payload: StatusUpdate):
    """
    Update alert status.
    - Acknowledge: allowed without remarks (sets acknowledgedAt)
    - Resolved: requires remarks (sets resolvedAt and saves remarks)
    """
    ref = db.collection("alerts").document(alert_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Alert not found")

    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    update = {}
    s = payload.status

    if s == "Acknowledge":
        update["status"] = "Acknowledge"
        update["acknowledgedAt"] = now_iso
    elif s == "Resolved":
        if not payload.remarks or not payload.remarks.strip():
            raise HTTPException(status_code=400, detail="Remarks required to resolve")
        update["status"] = "Resolved"
        update["remarks"] = payload.remarks.strip()
        update["resolvedAt"] = now_iso
    else:
        raise HTTPException(status_code=400, detail="Invalid status")

    try:
        ref.update(update)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "status": update["status"]}