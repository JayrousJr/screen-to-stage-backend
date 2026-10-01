from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.db.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Screen-to-Stage Backend", lifespan=lifespan)
app.include_router(health.router, prefix="/api")
