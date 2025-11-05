import threading
import time
import logging
from typing import List, Dict, Any
import cv2
import numpy as np
from config import STREAM_URL, MODEL_SERVICE_URL
from services.model_client import predict_frame_via_service
from services.processor import process_frame_from_model_response

logger = logging.getLogger("services.background")

# Shared state for latest detections and violation log
_latest_detection_lock = threading.Lock()
_latest_detection: Dict[str, Any] = {"timestamp": 0, "detections": [], "frame": None}
_violation_log_lock = threading.Lock()
_violation_log: List[Dict[str, Any]] = []

def get_latest_detection() -> Dict[str, Any]:
    with _latest_detection_lock:
        return dict(_latest_detection)

def get_violation_log() -> List[Dict[str, Any]]:
    with _violation_log_lock:
        return list(_violation_log)

def background_detection_loop(interval: float = 10.0):
    """
    Continuously fetch frames, run detection, aggregate every `interval` seconds.
    """
    logger.info("Starting background detection loop with interval %.1fs", interval)
    buffer: List[Dict[str, Any]] = []
    last_agg_time = time.time()
    while True:
        try:
            # Fetch frame from IP camera
            r = None
            try:
                r = cv2.VideoCapture(STREAM_URL)
                ret, frame = r.read()
                if not ret or frame is None:
                    logger.warning("Failed to fetch frame from stream")
                    time.sleep(1)
                    continue
            finally:
                if r is not None:
                    r.release()
            # Call model API
            try:
                model_resp = predict_frame_via_service(MODEL_SERVICE_URL, frame)
            except Exception as e:
                logger.warning(f"Model API call failed: {e}")
                time.sleep(1)
                continue
            # Store latest detection (thread-safe)
            with _latest_detection_lock:
                _latest_detection["timestamp"] = time.time()
                _latest_detection["detections"] = model_resp.get("detections", [])
                _latest_detection["frame"] = frame
            # Buffer detections for aggregation
            buffer.append({"detections": model_resp.get("detections", []), "frame": frame, "timestamp": time.time()})
            # Aggregate every interval seconds
            now = time.time()
            if now - last_agg_time >= interval:
                # Aggregate detections in buffer
                all_detections = [item["detections"] for item in buffer if item["detections"]]
                flat_detections = [det for sublist in all_detections for det in sublist]
                # Log violations (use processor logic if needed)
                violation_entry = {
                    "timestamp": now,
                    "detections": flat_detections,
                }
                with _violation_log_lock:
                    _violation_log.append(violation_entry)
                    # Keep log to last 100 entries
                    if len(_violation_log) > 100:
                        _violation_log.pop(0)
                # TODO: Send Brevo email alert here (placeholder)
                logger.info(f"Aggregated {len(flat_detections)} detections at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}")
                buffer.clear()
                last_agg_time = now
            time.sleep(1)
        except Exception as e:
            logger.error(f"Background detection loop error: {e}")
            time.sleep(2)

def start_background_detection():
    t = threading.Thread(target=background_detection_loop, daemon=True)
    t.start()
    logger.info("Background detection thread started.")