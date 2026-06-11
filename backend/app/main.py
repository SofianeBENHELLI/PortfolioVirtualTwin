import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import Base, engine
from app.core.events import bus
from app.monitor.scheduler import monitor_loop
from app.routers import agents, auth, backtests, macro, portfolios, strategies, system, watchlist

logging.basicConfig(level=logging.INFO)
settings = get_settings()


def _micro_migrations() -> None:
    """Tiny additive migrations for dev DBs created before a column existed.
    (Real migrations move to Alembic once the schema stabilizes.)"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    additive = {
        "portfolios": [
            ("kind", "VARCHAR(12) DEFAULT 'paper'"),
            ("live_armed", "BOOLEAN DEFAULT 0"),
            ("max_order_notional", "FLOAT DEFAULT 1000.0"),
            ("max_live_orders_per_day", "INTEGER DEFAULT 5"),
        ],
        "order_proposals": [("risk_score", "FLOAT"), ("risk_factors", "JSON")],
        "recommendations": [("risk_score", "FLOAT")],
    }
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in additive.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        if "portfolios" in tables:
            conn.execute(text("UPDATE portfolios SET broker = 'manual' WHERE broker = 'none'"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.models  # noqa: F401 — register all tables
    Base.metadata.create_all(engine)
    _micro_migrations()
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
app.include_router(macro.router)
app.include_router(system.router)
