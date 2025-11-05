from fastapi import FastAPI, UploadFile, File
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from routes import router

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
    """
    Receives an image file from the frontend, sends it to the 
    Hugging Face model, and returns the detection results.
    """
    
    # Read the image bytes from the uploaded file
    image_bytes = await file.read()
    
    # --- YOUR LOGIC HERE ---
    # 1. Send image_bytes to your Hugging Face model endpoint
    # results = await get_hf_prediction(image_bytes)
    
    # 2. Get the JSON response from the model
    # For testing, let's pretend we got this result:
    results = {
        "detections": [
            {"class": "Improper Hard Hat", "box": [100, 150, 150, 200]},
            {"class": "No Vest", "box": [200, 300, 400, 500]}
        ]
    }
    # --- END OF YOUR LOGIC ---

    return results

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)