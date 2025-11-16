import datetime
import logging
from zoneinfo import ZoneInfo
from config import CAMERA_ID, TZ
from services.storage import add_violation
from services.emailer import send_alert

logger = logging.getLogger("services.processor")





COMPLIANCE_CLASSES = {
    'Proper Hard Hat', 
    'Proper Reflectorized Vest',
    'Proper Safety Glasses',
    'Proper Safety Gloves',
    'Proper Safety Shoes'
}

NONCOMPLIANCE_CLASSES = {
    'Improper Hard Hat',
    'Improper Safety Glasses',
    'Improper Safety Gloves',
    'Improper Safety Shoes',
    'Improper Reflectorized Vest',
    'Non-PPE Hat',
    'No Hard Hat',
    'No Reflectorized Vest',
    'No Safety Glasses',
    'No Safety Gloves'
}


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
        if i in seen:
            continue
        keep = det
        x1,y1,x2,y2 = det["xmin"], det["ymin"], det["xmax"], det["ymax"]
        cls_name, conf = det["name"], det["confidence"]

        for j, other in enumerate(detections):
            if j <= i or j in seen:
                continue
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

def normalize_detections(resp):

    raw = resp.get("predictions") or resp.get("detections") or []
    out = []

    for d in raw:
        name = d.get("label") or d.get("name") or d.get("class")
        conf = float(d.get("confidence") or d.get("score") or 0.0)

        if "xmin" in d and "ymin" in d and "xmax" in d and "ymax" in d:
            xmin, ymin, xmax, ymax = d["xmin"], d["ymin"], d["xmax"], d["ymax"]
        elif "bbox" in d and isinstance(d["bbox"], (list,tuple)) and len(d["bbox"])>=4:
            xmin,ymin,xmax,ymax = d["bbox"][:4]
        elif all(k in d for k in ("x","y","w","h")):
            xmin = d["x"]; ymin = d["y"]; xmax = d["x"] + d["w"]; ymax = d["y"] + d["h"]
        else:
            continue



        out.append({
            "name": name,
            "confidence": conf,
            "xmin": float(xmin),
            "ymin": float(ymin),
            "xmax": float(xmax),
            "ymax": float(ymax)
        })
    return out


def process_frame_from_model_response(model_resp: dict, background_tasks=None, dedupe_window_seconds: int = 30):
    """
    PROCESS business logic only. Does NOT call the model.
    model_resp: raw response from predict_frame_via_service
    """
    detections = normalize_detections(model_resp)
    filtered = filter_overlaps(detections)
    now_iso = datetime.datetime.now(ZoneInfo(TZ)).isoformat() if TZ else datetime.datetime.utcnow().isoformat()
    violations = []
    compliance = []
    for det in filtered:
        if det["name"] in NONCOMPLIANCE_CLASSES:
            confidence_pct = int(round(det["confidence"] * 100)) if det["confidence"] <= 1 else int(round(det["confidence"]))
            doc = {
                "type": det["name"],
                "confidence": confidence_pct,
                "bbox": [det["xmin"], det["ymin"], det["xmax"], det["ymax"]],
                "timestamp": now_iso,
                "camera_id": CAMERA_ID,
                # optional front-end friendly aliases
                "violationType": det["name"],
                "footageId": CAMERA_ID
            }
            doc_id = add_violation(doc, dedupe_window_seconds=dedupe_window_seconds)
            if doc_id:
                violations.append({**doc, "violationId": doc_id})
                if background_tasks is not None:
                    background_tasks.add_task(send_alert, ["smartsafety.alerts@gmail.com"], det["name"], confidence_pct, now_iso)
        elif det["name"] in COMPLIANCE_CLASSES:
            compliance.append(det)
    logger.debug("process_frame_from_model_response: compliance=%d, noncompliance=%d", len(compliance), len(violations))
    return {
        "violations_stored": len(violations),
        "violations": violations,
        "compliance": compliance,
        "total_detections": len(filtered)
    }