import logging, time, threading, cv2, numpy as np, os
import firebase_admin
import cloudinary, cloudinary.uploader
import tempfile, smtplib
from config import STREAM_URL, CAMERA_ID, MODEL_SERVICE_URL, FIREBASE_CRED_PATH
from config import CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
from config import BREVO_USER, BREVO_PASS, BREVO_SENDER
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from firebase_admin import credentials, firestore
from pydantic import BaseModel
from services.model_client import predict_frame_via_service
from services.processor import process_frame_from_model_response


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
detection_lock = threading.Lock()
is_on_cooldown = False
COOLDOWN_SECONDS = 20  # You can set this via env or config if needed

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
    # Format the timestamp string for the email body.
    try:
        timestamp_dt = datetime.datetime.fromisoformat(violation_data.get('timestamp'))
        formatted_datetime = timestamp_dt.strftime("%m/%d/%Y, %I:%M:%S %p")
    except Exception:
        formatted_datetime = violation_data.get('timestamp') or ''
    body = f"Hello Safety Officer,\n\nA new PPE violation has been detected.\n\nViolation: {violation_data.get('violationType')}\nConfidence: {violation_data.get('confidence')}%\nDate & Time: {formatted_datetime}\n- View Footage: {footage_url}\nThis violation has been logged into the SmartSafety system.\n\n\nPlease take appropriate action.\n\nStay safe,\nSmartSafety Monitoring System"
    msg = MIMEMultipart(); 
    # For display name of email since Python code overrides the name set in Brevo.
    sender_name = "SmartSafety Alerts System"
    sender_email = BREVO_SENDER
    msg['From'] = f"{sender_name} <{sender_email}>"
    msg['To'] = to_email; 
    msg['Subject'] = subject; 
    msg.attach(MIMEText(body, 'plain'))
    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as smtp: 
            smtp.starttls() 
            smtp.login(BREVO_USER, BREVO_PASS); 
            smtp.send_message(msg)
        print(f"INFO: Email sent for violation {violation_id}")
        db.collection("violations").document(violation_id).update({"alertSent": True})
    except Exception as e: print(f"ERROR sending email for {violation_id}: {e}")

#Cloudinary upload
def final_upload_and_update(temp_video_path, violation_docs):
    try:
        if temp_video_path:
            print("INFO (Thread): Uploading video to Cloudinary...")
            upload_result = cloudinary.uploader(temp_video_path, resource_type="video", folder="violations")
            public_id = upload_result.get("public_id")
            footage_url = f"https://res.cloudinary.com/{CLOUDINARY_CLOUD_NAME}/video/upload/f_mp4/{public_id}.mp4"
            
            if footage_url:
                print(f"INFO (Thread): {footage_url} generated. Updating violation docs...")
                for doc_id in violation_docs:
                    doc_ref = db.collection("violations").document(doc_id)
                    doc_ref.update({"footageUrl": footage_url})
                    snapshot = doc_ref.get()
                    
                    if snapshot.exists and not snapshot.to.dict().get("alertSent"):
                        send_email_alert_from_backend(snapshot.to_dict(), footage_url)
            else:
                print("INFO (Thread): Cooldown trigger only. No footage URL generated.")
                
    except Exception as e:
        print(f"ERROR in uploading thread: {e}")
        
    finally:
        if temp_video_path and isinstance(temp_video_path, str) and os.path.exists(temp_video_path):
            
            try:
                os.remove(temp_video_path)
                print("INFO (Thread): Temporary video file removed.") 
            
            except Exception as cleanup_error:
                print(f"ERROR removing temp video file: {cleanup_error}")  
              
# Camera stream
def start_camera_stream():
    global output_frame, STREAM_URL, last_frame_time, last_api_call_time
    
    # Use the sub-stream (102) and force TCP for stability
    # Ensure STREAM_URL in Render is: rtsp://user:pass@url.../Streaming/Channels/102
    # And set OPENCV_FFMPEG_CAPTURE_OPTIONS = rtsp_transport;tcp in Render env
    
    print(f"Starting background stream from: {STREAM_URL}")
    cap = None

    while True:
        current_time = time.time()
        
        if (current_time - last_api_call_time) > 10.0:
            if cap is not None:
                print("Use inactive. Waiting for API calls...")
                cap.release()
                cap = None
            time.sleep(1.0)
            continue
        
        if cap is None or not cap.isOpened():
            print("User active. Opening camera stream...")
            cap = cv2.VideoCapture(STREAM_URL)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            time.sleep(2.0)
            
        if cap is not None and cap.isOpened():
            ret, frame = cap.read()
            if ret:
                with lock:
                    output_frame = frame.copy()
                    last_frame_time = time.time()
            else:
                print("Lost connection to camera. Reconnecting...")
                cap.release()
                cap = None
                time.sleep(2)
        else:
            time.sleep(2)

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
    global latest_webcam_detection, last_api_call_time, is_on_cooldown, detection_lock
    last_api_call_time = time.time()

    # Try to acquire the lock non-blocking
    acquired = detection_lock.acquire(blocking=False)
    if not acquired or is_on_cooldown:
        if acquired:
            try:
                detection_lock.release()
            except RuntimeError:
                pass
        return JSONResponse({
            "violations_stored": 0,
            "unresolved": [],
            "detections": [],
            "width": 1920,
            "height": 1080,
            "message": "System is on cooldown or busy."
        }, status_code=429)

    try:
        if latest_webcam_detection is None:
            try:
                detection_lock.release()
            except RuntimeError:
                pass
            return JSONResponse({
                "violations_stored": 0,
                "unresolved": [],
                "detections": [],
                "width": 1920,
                "height": 1080
            })

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
        
        is_on_cooldown = True
        
        def upload_and_release(temp_video_path, violation_ids):
            global is_on_cooldown
            
            try:
                final_upload_and_update(temp_video_path, violation_ids)
                
            finally:
                # Enforce cooldown period and then release the detection lock
                try:
                    print(f"INFO: Starting {COOLDOWN_SECONDS}-second cooldown.")
                    time.sleep(COOLDOWN_SECONDS)
                except Exception as e:
                    print(f"WARN: cooldown sleep interrupted: {e}")

                is_on_cooldown = False
                try:
                    if detection_lock.locked():
                        detection_lock.release()
                except RuntimeError:
                    pass
                print("INFO: Cooldown finished. System is ready.")
            
        background_tasks.add_task(upload_and_release, None, []) 
        return JSONResponse({
            "violations_stored": result.get("violations_stored", 0),
            "unresolved": [v.get("violationType") or v.get("type") for v in violations],
            "detections": latest_webcam_detection.get("detections", []),
            "width": latest_webcam_detection.get("width", 1920),
            "height": latest_webcam_detection.get("height", 1080)
       })
        
    except Exception as e:
        logger.exception("detect_ipcam: processing failed")
        is_on_cooldown = False
        try:
            if detection_lock.locked():
                detection_lock.release()
        except RuntimeError:
            pass
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
    global output_frame, lock
    
    if output_frame is None:
        return JSONResponse({"error": "No frames available from webcam."}, status_code=400)

    new_width, new_height = 1920, 1080
    fps = 20.0
    total_frames_to_record = int(fps * 20.0)
    
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
        with lock:
            if output_frame is None:
                resized_frame = cv2.resize(frame, (new_width, new_height))
                out.write(resized_frame)
                frames_recorded += 1
                
        time.sleep(1.0 / fps)
        
    out.release()

    background_tasks.add_task(final_upload_and_update, temp_video_path, violation_ids)
    return JSONResponse({"message": "Video recorded and upload started."})

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