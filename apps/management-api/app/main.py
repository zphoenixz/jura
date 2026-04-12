import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import config, epics, epics_police, health, linear, meets, people, slack

app = FastAPI(title="Management API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static directory for UI assets
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(health.router)
app.include_router(config.router)
app.include_router(people.router)
app.include_router(slack.router)
app.include_router(linear.router)
app.include_router(meets.router)
app.include_router(epics.router)
app.include_router(epics_police.router)
