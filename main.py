from fastapi import FastAPI
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