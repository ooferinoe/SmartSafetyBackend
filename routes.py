from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
import datetime, threading, tempfile, cv2, time, os
from zoneinfo import ZoneInfo
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Import shared objects from shared.py
from shared import model, db, violations_ref, STREAM_URL, UNRESOLVED_CLASSES, cloud_name, GMAIL_USER, GMAIL_PASS

router = APIRouter()

detection_lock = threading.Lock()
is_on_cooldown = False

BOX_COLOR = (0, 0, 255)
TEXT_COLOR = (0, 255, 255)
LABEL_BG_COLOR = (0, 0, 0)

def compute_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0]); yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2]); yB = min(boxA[3], boxB[3])
    interW = max(0, xB - xA); interH = max(0, yB - yA)
    interArea = interW * interH
    boxAArea = max(0, boxA[2]-boxA[0]) * max(0, boxA[3]-boxA[1])
    boxBArea = max(0, boxB[2]-boxB[0]) * max(0, boxB[3]-boxB[1])
    denom = boxAArea + boxBArea - interArea
    return 0.0 if denom <= 0 else interArea / denom

def filter_overlaps(detections):
    filtered = []
    seen = set()
    for i, det in enumerate(detections):
        if i in seen: continue
        keep = det
        x1,y1,x2,y2 = det["xmin"], det["ymin"], det["xmax"], det["ymax"]
        cls_name, conf = det["name"], det["confidence"]
        for j, other in enumerate(detections):
            if j <= i or j in seen: continue
            iou = compute_iou((x1,y1,x2,y2),(other["xmin"],other["ymin"],other["xmax"],other["ymax"]))
            if iou > 0.5 and (
                (cls_name == "Safety Glasses" and other["name"] == "Improper Safety Glasses") or
                (cls_name == "Improper Safety Glasses" and other["name"] == "Safety Glasses")
            ):
                if other["confidence"] > conf:
                    keep = other
                    conf = other["confidence"]
                seen.add(j)
        filtered.append(keep)
    return filtered

def send_email_alert_from_backend(violation_data, footage_url):
    violation_id = violation_data.get("violationId")
    to_email = (violation_data.get("alertSentTo") or [])[0] if violation_data.get("alertSentTo") else None
    if not to_email: return
    subject = f"Violation Alert: {violation_data.get('violationType')}"
    timestamp_dt = datetime.datetime.fromisoformat(violation_data.get('timestamp'))
    formatted_datetime = timestamp_dt.strftime("%m/%d/%Y, %I:%M:%S %p")
    body = f"Hello Safety Officer,\n\nA new PPE violation has been detected.\n\nViolation: {violation_data.get('violationType')}\nConfidence: {violation_data.get('confidence')}%\nDate & Time: {formatted_datetime}\n- View Footage: {footage_url}\nThis violation has been logged into the SmartSafety system.\n\n\nPlease take appropriate action.\n\nStay safe,\nSmartSafety Monitoring System"
    msg = MIMEMultipart(); msg['From'] = GMAIL_USER; msg['To'] = to_email; msg['Subject'] = subject; msg.attach(MIMEText(body, 'plain'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(GMAIL_USER, GMAIL_PASS); smtp.send_message(msg)
        print(f"INFO: Email sent for violation {violation_id}")
        violations_ref.document(violation_id).update({"alertSent": True})
    except Exception as e: print(f"ERROR sending email for {violation_id}: {e}")

def final_upload_and_update(temp_video_path, violation_docs):
    global is_on_cooldown
    try:
        import cloudinary.uploader
        print("INFO (Thread): Uploading AVI to Cloudinary...")
        upload_result = cloudinary.uploader.upload(temp_video_path, resource_type="video", folder="violations")
        public_id = upload_result.get('public_id')
        footage_url = f"https://res.cloudinary.com/{cloud_name}/video/upload/f_mp4/{public_id}.mp4"
        if footage_url:
            print(f"INFO (Thread): MP4 URL generated. Updating docs and sending email...")
            for doc_id in violation_docs:
                doc_ref = violations_ref.document(doc_id)
                doc_ref.update({"footageUrl": footage_url})
                snapshot = doc_ref.get()
                if snapshot.exists and not snapshot.to_dict().get("alertSent"):
                    send_email_alert_from_backend(snapshot.to_dict(), footage_url)
    except Exception as e:
        print(f"FATAL ERROR in upload thread: {e}")
    finally:
        os.remove(temp_video_path)
        print("INFO (Thread): Upload task finished and temp file deleted.")
        print("INFO: Starting 30-second cooldown.")
        time.sleep(30)
        is_on_cooldown = False
        detection_lock.release()
        print("INFO: Cooldown finished. System is ready.")

def process_upload_and_alert(violation_docs):
    print("INFO: SMOOTH background task with OVERLAP FILTERING started...")
    cap = cv2.VideoCapture(STREAM_URL)
    if not cap.isOpened():
        print("ERROR: Background task could not open stream.")
        detection_lock.release()
        global is_on_cooldown
        is_on_cooldown = False
        return
    try:
        new_width, new_height = 1920, 1080
        fps, total_frames_to_record = 20.0, int(20 * 20.0)
        temp_video = tempfile.NamedTemporaryFile(suffix=".avi", delete=False)
        temp_video_path = temp_video.name
        temp_video.close()
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        out = cv2.VideoWriter(temp_video_path, fourcc, fps, (new_width, new_height))
        if not out.isOpened():
            print("FATAL ERROR: cv2.VideoWriter failed.")
            cap.release()
            detection_lock.release()
            is_on_cooldown = False
            return
        print(f"INFO: Recording {total_frames_to_record} frames with custom visuals...")
        frames_recorded, last_detections = 0, []
        detection_interval = 5
        while frames_recorded < total_frames_to_record:
            ret, frame = cap.read()
            if not ret: break
            resized_frame = cv2.resize(frame, (new_width, new_height))
            if frames_recorded % detection_interval == 0:
                results = model(cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB))
                raw_detections = results.pandas().xyxy[0].to_dict(orient="records")
                last_detections = filter_overlaps(raw_detections)
            if last_detections:
                for det in last_detections:
                    if det['name'] in UNRESOLVED_CLASSES:
                        x1, y1 = int(det['xmin']), int(det['ymin'])
                        x2, y2 = int(det['xmax']), int(det['ymax'])
                        cv2.rectangle(resized_frame, (x1, y1), (x2, y2), BOX_COLOR, 2)
                        display_text = f"{det['name']} ({det['confidence']:.1%})"
                        (text_width, text_height), baseline = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                        cv2.rectangle(resized_frame, (x1, y1 - text_height - baseline - 5), (x1 + text_width, y1), LABEL_BG_COLOR, -1)
                        cv2.putText(resized_frame, display_text, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT_COLOR, 1)
            out.write(resized_frame)
            frames_recorded += 1
        out.release()
        print(f"INFO: Finished recording. Starting upload and cooldown process.")
        upload_thread = threading.Thread(target=final_upload_and_update, args=(temp_video_path, violation_docs))
        upload_thread.start()
    except Exception as e:
        print(f"FATAL ERROR in main background task: {e}")
        is_on_cooldown = False
        detection_lock.release()
    finally:
        if cap.isOpened(): cap.release()

@router.get("/detect_ipcam")
def detect_ipcam(background_tasks: BackgroundTasks):
    global is_on_cooldown
    if is_on_cooldown or not detection_lock.acquire(blocking=False):
        return JSONResponse(content={"message": "System is on cooldown or busy."}, status_code=429)
    try:
        cap = cv2.VideoCapture(STREAM_URL)
        if not cap.isOpened(): return JSONResponse(content={"error": "Failed to open stream"}, status_code=500)
        ret, frame = cap.read(); cap.release()
        if not ret: return JSONResponse(content={"error": "Failed to get frame"}, status_code=500)
        results = model(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        detections = filter_overlaps(results.pandas().xyxy[0].to_dict(orient="records"))
        unresolved_detections = [d for d in detections if d['name'] in UNRESOLVED_CLASSES]
        if not unresolved_detections:
            detection_lock.release()
            return JSONResponse(content={"message": "No new unresolved violations."})
        is_on_cooldown = True
        print(f"INFO: Detected {len(unresolved_detections)} violation(s). Locking system and starting background task.")
        ph_time = datetime.datetime.now(ZoneInfo("Asia/Manila"))
        violations_logged, violation_doc_ids = [], []
        for det in unresolved_detections:
            violation_data = { "timestamp": ph_time.isoformat(), "date": ph_time.strftime("%m/%d/%Y"), "violationType": det["name"], "status": "Unresolved", "confidence": f"{det['confidence'] * 100:.0f}", "footageId": "CAM 001", "alertSentTo": ["danieljosesagun@gmail.com"], "alertSent": False, "footageUrl": None }
            doc_ref = violations_ref.document()
            violation_data["violationId"] = doc_ref.id
            doc_ref.set(violation_data)
            violations_logged.append(violation_data)
            violation_doc_ids.append(doc_ref.id)
        background_tasks.add_task(process_upload_and_alert, violation_docs=violation_doc_ids)
        return JSONResponse(content={"violations_logged": violations_logged})
    except Exception as e:
        print(f"ERROR in detect_ipcam: {e}")
        is_on_cooldown = False
        if detection_lock.locked():
            detection_lock.release()
        return JSONResponse(content={"error": "An internal error occurred."}, status_code=500)

@router.get("/get_frame_detections")
def get_frame_detections():
    cap = cv2.VideoCapture(STREAM_URL)
    if not cap.isOpened(): return JSONResponse(content={"error": "Stream is busy or unavailable"}, status_code=503)
    ret, frame = cap.read(); cap.release()
    if not ret: return JSONResponse(content={"error": "Failed to get frame"}, status_code=500)
    results = model(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    detections = filter_overlaps(results.pandas().xyxy[0].to_dict(orient="records"))
    response = [{"xmin": int(d["xmin"]),"ymin": int(d["ymin"]),"xmax": int(d["xmax"]),"ymax": int(d["ymax"]),"confidence": float(d["confidence"]),"label": d["name"]} for d in detections]
    height, width = frame.shape[:2]
    return JSONResponse(content={"detections": response, "width": width, "height": height})

