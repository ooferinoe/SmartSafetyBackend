import logging, time, threading, cv2, numpy as np, os, firebase_admin, cloudinary, cloudinary.uploader, tempfile, smtplib
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from config import STREAM_URL, CAMERA_ID, MODEL_SERVICE_URL, CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, FIREBASE_CRED_PATH, GMAIL_USER, GMAIL_PASS
from services.model_client import predict_frame_via_service
from services.processor import process_frame_from_model_response
from pydantic import BaseModel
from firebase_admin import credentials, firestore
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Cloudinary config
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True
)

router = APIRouter()
logger = logging.getLogger("routes")

# Global varibales
latest_webcam_detection = None
output_frame = None
last_frame_time =0
last_api_call_time = 0
lock = threading.Lock()

# Firebase initialization
cred = credentials.Certificate(FIREBASE_CRED_PATH)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Email alert function
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

#Cloudinary upload
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

# Camera stream
def start_camera_stream():
    global output_frame, STREAM_URL
    
    # Use the sub-stream (102) and force TCP for stability
    # Ensure STREAM_URL in Render is: rtsp://user:pass@url.../Streaming/Channels/102
    # And set OPENCV_FFMPEG_CAPTURE_OPTIONS = rtsp_transport;tcp in Render env
    
    print(f"Starting background stream from: {STREAM_URL}")
    cap = cv2.VideoCapture(STREAM_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    time.sleep(2.0)

    while True:
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                with lock:
                    output_frame = frame.copy()
                    last_frame_time = time.time()
            else:
                print("Lost connection to camera. Reconnecting...")
                cap.release()
                time.sleep(2)
                cap = cv2.VideoCapture(STREAM_URL)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            print("Camera not open. Retrying...")
            time.sleep(2)
            cap = cv2.VideoCapture(STREAM_URL)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

#AI detection
def start_detction_loop():
    global output_frame, latest_webcam_detection, MODEL_SERVICE_URL
    print(f"Starting detection loop pointing to: {MODEL_SERVICE_URL}")
    
    last_processed_time = 0
    
    while True:
        current_time = time.time()
        
        if (current_time - last_api_call_time) > 10.0:
            time.sleep(1.0)
            continue
        
        if (current_time - last_frame_time) > 5.0:
            print("Camera stream inactive. Waiting for frames...")
            time.sleep(1.0)
            continue
        
        if last_frame_time <= last_processed_time:
            time.sleep(0.05)
            continue
        
        frame_to_process = None
        with lock:
            if output_frame is not None:
                frame_to_process = output_frame.copy()
                last_processed_time = last_frame_time
                
        if frame_to_process is not None:
            try:
                result = predict_frame_via_service(MODEL_SERVICE_URL, frame_to_process)
                if result:
                    det_cout = len(result.get("detections", []))
                    print(f"Detection loop: got {det_cout} detections")
                    
                else:
                    print("Detection loop: no result from model service")
                    
                if result and not result.get("error"):
                    latest_webcam_detection = result
                    
            except Exception as e:
                logger.error(f"Detection loop error: {e}")
        
        time.sleep(0.5)
        
camStream = threading.Thread(target=start_camera_stream, daemon=True)
camStream.start()

detectionLoop = threading.Thread(target=start_detction_loop, daemon=True)
detectionLoop.start()

# Endpoints
@router.get("/detect_ipcam")
def detect_ipcam(background_tasks: BackgroundTasks):
    
    global latest_webcam_detection
    
    if latest_webcam_detection is None:
        return JSONResponse({
            "violations_stored": 0,
            "unresolved": [],
            "detections": [],
            "width": 1920,
            "height": 1080
        })
    
    try:
        result = process_frame_from_model_response(latest_webcam_detection, background_tasks=background_tasks)
        violations = result.get("violations", [])
        
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
            "unresolved": [v.get("violationType") or v.get("type") for v in violations],
            "detections": latest_webcam_detection.get("detections", []),
            "width": latest_webcam_detection.get("width", 1920),
            "height": latest_webcam_detection.get("height", 1080)
        })
        
    except Exception as e:
        logger.exception("detect_ipcam: processing failed")

        return JSONResponse({
            "violations_stored": 0,
            "unresolved": [],
            "detections": [],
            "error": str(e)
        })
        
def generate_frames():
    global output_frame, lock
    
    while True:
        with lock:
            if output_frame is None:
                time.sleep(0.1)
                continue
            (flag, encodedImage) = cv2.imencode(".jpg", output_frame)
            
        if not flag:
            continue

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encodedImage) + b'\r\n')
        time.sleep(0.10)

@router.get("/video_feed")
def video_feed():

    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

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