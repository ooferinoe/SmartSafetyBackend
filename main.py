from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared import model, db, violations_ref, STREAM_URL, UNRESOLVED_CLASSES, cloud_name, GMAIL_USER, GMAIL_PASS

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from routes import router
app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)