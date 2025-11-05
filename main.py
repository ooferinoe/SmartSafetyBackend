from fastapi import FastAPI, UploadFile, File
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from routes import router
from services.model_client import predict_frame_via_service

app = FastAPI()

origins = [
    "https://stgsmartsafety.netlify.app/",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

@app.post("/detect")
async def detect_ppe_violation(file: UploadFile = File(...)):
    image_bytes = await file.read()
    # Convert bytes to numpy array for OpenCV
    nparr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    results = predict_frame_via_service(MODEL_SERVICE_URL, frame)
    return results

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)