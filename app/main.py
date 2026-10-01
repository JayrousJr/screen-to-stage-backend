from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import errors, health, xray
from app.db.database import init_db
from app.services import worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Screen-to-Stage Backend", lifespan=lifespan)
errors.register(app)
app.include_router(health.router, prefix="/api")
app.include_router(xray.router, prefix="/api")
