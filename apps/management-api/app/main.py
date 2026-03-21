from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import config, epics, epics_police, health, linear, meets, people, slack

app = FastAPI(title="Management API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(config.router)
app.include_router(people.router)
app.include_router(slack.router)
app.include_router(linear.router)
app.include_router(meets.router)
app.include_router(epics.router)
app.include_router(epics_police.router)
