from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

@app.post("/predict")
async def predict(file: UploadFile = File(...)):
    # Return a deterministic, plausible response your backend expects
    return JSONResponse({
        "predictions": [
            {"label": "person", "confidence": 0.92, "bbox": [50, 40, 120, 300]},
            {"label": "hard_hat", "confidence": 0.80, "bbox": [60, 45, 40, 40]}
        ],
        "frame_id": "mock_frame_1"
    })