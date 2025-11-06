from fastapi import FastAPI, UploadFile, File
import uvicorn, asyncio, cv2, os, numpy as np
from fastapi.middleware.cors import CORSMiddleware
from routes import router
from services.model_client import predict_frame_via_service
from contextlib import asynccontextmanager

# async def run_model_detection(image_bytes):
#     nparr = np.frombuffer(image_bytes, np.uint8)
#     frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
#     results = predict_frame_via_service(os.environ.get("MODEL_SERVICE_URL"), frame)
#     return results

# async def monitor_camera_stream(camera_url):
#     print(f"Starting to monitor stream: {camera_url}")
#     cap = cv2.VideoCapture(camera_url)
#     while True:
#         success, frame = cap.read()
#         if not success:
#             print("Stream ended or failed. Reconnecting...")
#             await asyncio.sleep(5)
#             cap.release()
#             cap = cv2.VideoCapture(camera_url)
#             continue
#         _, image_bytes = cv2.imencode('.jpg', frame)
#         await run_model_detection(image_bytes.tobytes())
#         await asyncio.sleep(0.5)
        
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     # Startup logic
#     PRODUCTION_CAMERA_URL = os.environ.get("STREAM_URL", "http://192.168.1.14:8080/video")
#     print("Starting background camera monitor...")
#     asyncio.create_task(monitor_camera_stream(PRODUCTION_CAMERA_URL))
#     yield
#     # Shutdown logic (if needed)
    
app = FastAPI()

origins = ["https://smartsafetystg.netlify.app"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# @app.post("/detect")
# async def detect_ppe_violation(file: UploadFile = File(...)):
#     image_bytes = await file.read()
#     # Convert bytes to numpy array for OpenCV
#     nparr = np.frombuffer(image_bytes, np.uint8)
#     frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
#     results = predict_frame_via_service(os.environ.get("MODEL_SERVICE_URL"), frame)
#     return results

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)