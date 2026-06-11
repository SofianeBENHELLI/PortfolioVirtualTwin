import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import Base, engine
from app.core.events import bus
from app.monitor.scheduler import monitor_loop
from app.routers import agents, auth, backtests, portfolios, strategies, system, watchlist

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.models  # noqa: F401 — register all tables
    Base.metadata.create_all(engine)
    bus.set_loop(asyncio.get_running_loop())
    task = asyncio.create_task(monitor_loop())
    yield
    task.cancel()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(strategies.router)
app.include_router(portfolios.router)
app.include_router(backtests.router)
app.include_router(agents.router)
app.include_router(watchlist.router)
app.include_router(system.router)
