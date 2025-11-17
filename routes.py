import logging, time, threading, cv2, requests, numpy as np, os
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
import firebase_admin
from firebase_admin import credentials, firestore
import cloudinary, cloudinary.uploader
from config import CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET

# Cloudinary config
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True
)

from config import FIREBASE_CRED, GMAIL_USER, GMAIL_PASS
cred = credentials.Certificate(FIREBASE_CRED)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()
import tempfile
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
def send_email_alert_from_backend(violation_data, footage_url):
    violation_id = violation_data.get("violationId")
    to_email = (violation_data.get("alertSentTo") or [])[0] if violation_data.get("alertSentTo") else None
    if not to_email: return
    subject = f"Violation Alert: {violation_data.get('violationType')}"
    timestamp_dt = datetime.fromisoformat(violation_data.get('timestamp'))
    formatted_datetime = timestamp_dt.strftime("%m/%d/%Y, %I:%M:%S %p")
    body = f"Hello Safety Officer,\n\nA new PPE violation has been detected.\n\nViolation: {violation_data.get('violationType')}\nConfidence: {violation_data.get('confidence')}%\nDate & Time: {formatted_datetime}\n- View Footage: {footage_url}\nThis violation has been logged into the SmartSafety system.\n\n\nPlease take appropriate action.\n\nStay safe,\nSmartSafety Monitoring System"
    msg = MIMEMultipart(); msg['From'] = GMAIL_USER; msg['To'] = to_email; msg['Subject'] = subject; msg.attach(MIMEText(body, 'plain'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(GMAIL_USER, GMAIL_PASS); smtp.send_message(msg)
        print(f"INFO: Email sent for violation {violation_id}")
        db.collection("violations").document(violation_id).update({"alertSent": True})
    except Exception as e: print(f"ERROR sending email for {violation_id}: {e}")
def final_upload_and_update(temp_video_path, violation_docs):
    try:
        print("INFO (Thread): Uploading AVI to Cloudinary...")
        upload_result = cloudinary.uploader.upload(temp_video_path, resource_type="video", folder="violations")
        public_id = upload_result.get('public_id')
        footage_url = f"https://res.cloudinary.com/{CLOUDINARY_CLOUD_NAME}/video/upload/f_mp4/{public_id}.mp4"
        if footage_url:
            print(f"INFO (Thread): MP4 URL generated. Updating docs and sending email...")
            for doc_id in violation_docs:
                doc_ref = db.collection("violations").document(doc_id)
                doc_ref.update({"footageUrl": footage_url})
                snapshot = doc_ref.get()
                if snapshot.exists and not snapshot.to_dict().get("alertSent"):
                    send_email_alert_from_backend(snapshot.to_dict(), footage_url)
    except Exception as e:
        print(f"FATAL ERROR in upload thread: {e}")
    finally:
        os.remove(temp_video_path)
        print("INFO (Thread): Upload task finished and temp file deleted.")
        
@router.post("/upload_video")
async def upload_video(background_tasks: BackgroundTasks, violation_ids: list):
    """
    Records a short video from the webcam, uploads to Cloudinary, and updates violation docs with footage URL.
    Accepts a list of violation document IDs to update.
    """
    cap = cv2.VideoCapture(STREAM_URL)
    if not cap.isOpened():
        return JSONResponse({"error": "Failed to open webcam stream."}, status_code=500)
    new_width, new_height = 1920, 1080
    fps, total_frames_to_record = 20.0, int(20 * 20.0)
    temp_video = tempfile.NamedTemporaryFile(suffix=".avi", delete=False)
    temp_video_path = temp_video.name
    temp_video.close()
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(temp_video_path, fourcc, fps, (new_width, new_height))
    if not out.isOpened():
        cap.release()
        return JSONResponse({"error": "Failed to initialize video writer."}, status_code=500)
    frames_recorded = 0
    while frames_recorded < total_frames_to_record:
        ret, frame = cap.read()
        if not ret:
            break
        resized_frame = cv2.resize(frame, (new_width, new_height))
        out.write(resized_frame)
        frames_recorded += 1
    out.release()
    cap.release()
    background_tasks.add_task(final_upload_and_update, temp_video_path, violation_ids)
    return JSONResponse({"message": "Video recorded and upload started."})
from datetime import datetime, timezone

class StatusUpdate(BaseModel):
    status: str
    remarks: str = None

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
    Captures a frame from the IP camera (supports both HTTP image and RTSP stream), sends it to the model API, and returns detection results.
    """
    frame = None
    if STREAM_URL.lower().startswith("http"):
        try:
            r = requests.get(STREAM_URL, timeout=5)
            r.raise_for_status()
            jpg = np.frombuffer(r.content, dtype=np.uint8)
            frame = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
            if frame is None:
                return {"error": "Failed to decode image from HTTP URL.", "details": f"Content length: {len(r.content)}"}
        except requests.exceptions.ConnectionError as ce:
            return {"error": "Connection error to camera URL.", "details": str(ce)}
        except requests.exceptions.Timeout as te:
            return {"error": "Timeout when connecting to camera URL.", "details": str(te)}
        except requests.exceptions.RequestException as re:
            return {"error": "Request error when connecting to camera URL.", "details": str(re)}
        except Exception as e:
            return {"error": f"Failed to fetch image from HTTP URL: {e}", "details": str(e)}
    elif STREAM_URL.lower().startswith("rtsp"):
        cap = cv2.VideoCapture(STREAM_URL)
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            return {"error": "Failed to capture frame from RTSP stream."}
    else:
        return {"error": "Unsupported STREAM_URL protocol. Use http or rtsp."}

    result = predict_frame_via_service(MODEL_SERVICE_URL, frame)
    return result


# --- Violation status update route ---
@router.patch("/violations/{violation_id}/status")
async def update_violation_status(violation_id: str, payload: StatusUpdate):
    """
    Update violation status and remarks directly in the violations collection.
    - Acknowledge: allowed without remarks (sets acknowledgedAt)
    - Resolved: requires remarks (sets resolvedAt and saves remarks)
    """
    ref = db.collection("violations").document(violation_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Violation not found")

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
        update["resolvedAt"] = now_iso
        update["remarks"] = payload.remarks.strip()
    else:
        raise HTTPException(status_code=400, detail="Invalid status")

    try:
        ref.update(update)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "status": update["status"]}

@router.get("/health")
def health_check():
    return JSONResponse({"status": "healthy"})

