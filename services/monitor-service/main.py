import os
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("monitor-service")
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pulsarops:changeme@postgres:5432/pulsarops",
)
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "30"))
LATENCY_THRESHOLD_MS = int(os.getenv("LATENCY_THRESHOLD_MS", "2000"))

TARGETS = [
    {"name": "google", "url": "https://www.google.com"},
    {"name": "github", "url": "https://github.com"},
    {"name": "aws", "url": "https://aws.amazon.com"},
    {"name": "cloudflare", "url": "https://www.cloudflare.com"},
    {"name": "azure", "url": "https://azure.microsoft.com"},
    {"name": "grafana", "url": "https://grafana.com"},
]

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
CHECK_TOTAL = Counter(
    "monitor_checks_total",
    "Total health checks performed",
    ["target", "status"],
)
CHECK_LATENCY = Histogram(
    "monitor_check_duration_seconds",
    "HTTP check latency per target",
    ["target"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)
TARGET_UP = Gauge(
    "monitor_target_up",
    "1 = target is reachable, 0 = target is down",
    ["target"],
)

# ---------------------------------------------------------------------------
# DB pool (initialised in lifespan)
# ---------------------------------------------------------------------------
db_pool: asyncpg.Pool | None = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS health_checks (
    id          BIGSERIAL PRIMARY KEY,
    target      TEXT        NOT NULL,
    url         TEXT        NOT NULL,
    status      TEXT        NOT NULL,
    status_code INTEGER,
    latency_ms  NUMERIC(10,2),
    error       TEXT,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_health_checks_target ON health_checks(target);
CREATE INDEX IF NOT EXISTS ix_health_checks_checked_at ON health_checks(checked_at DESC);
"""


async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=2, max_size=10, command_timeout=30
    )
    async with db_pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)
    logger.info("Database pool initialised and schema ensured")


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------
async def check_target(target: dict) -> dict:
    name = target["name"]
    url = target["url"]
    start = time.perf_counter()
    result = {
        "target": name,
        "url": url,
        "status": "unknown",
        "status_code": None,
        "latency_ms": None,
        "error": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
        latency_ms = (time.perf_counter() - start) * 1000
        result["status_code"] = resp.status_code
        result["latency_ms"] = round(latency_ms, 2)

        if resp.status_code < 400 and latency_ms <= LATENCY_THRESHOLD_MS:
            result["status"] = "healthy"
            TARGET_UP.labels(target=name).set(1)
        else:
            result["status"] = "degraded"
            TARGET_UP.labels(target=name).set(0)

        CHECK_LATENCY.labels(target=name).observe(latency_ms / 1000)
        CHECK_TOTAL.labels(target=name, status=result["status"]).inc()

    except Exception as exc:
        result["status"] = "down"
        result["error"] = str(exc)
        TARGET_UP.labels(target=name).set(0)
        CHECK_TOTAL.labels(target=name, status="down").inc()
        logger.warning(f"Check failed for {name}: {exc}")

    return result


async def persist_result(result: dict):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO health_checks (target, url, status, status_code, latency_ms, error, checked_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            result["target"],
            result["url"],
            result["status"],
            result.get("status_code"),
            result.get("latency_ms"),
            result.get("error"),
            datetime.fromisoformat(result["checked_at"]),
        )


async def run_all_checks() -> list[dict]:
    results = await asyncio.gather(*[check_target(t) for t in TARGETS])
    await asyncio.gather(*[persist_result(r) for r in results])
    logger.info(f"Completed checks for {len(results)} targets")
    return list(results)


# ---------------------------------------------------------------------------
# Background polling loop
# ---------------------------------------------------------------------------
async def polling_loop():
    while True:
        try:
            await run_all_checks()
        except Exception as exc:
            logger.error(f"Polling loop error: {exc}")
        await asyncio.sleep(MONITOR_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Pre-warm: run one round immediately so data is available on first request
    asyncio.create_task(run_all_checks())
    asyncio.create_task(polling_loop())
    logger.info(f"Background polling started — interval {MONITOR_INTERVAL}s")
    yield
    await db_pool.close()
    logger.info("Database pool closed")


app = FastAPI(title="PulsarOps Monitor Service", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_status = "ok"
    except Exception:
        db_status = "unreachable"
    return {"service": "monitor-service", "status": "ok", "database": db_status}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/v1/targets")
async def list_targets():
    return {"targets": TARGETS}


@app.get("/api/v1/check")
async def check_all():
    results = await run_all_checks()
    summary = {
        "healthy": sum(1 for r in results if r["status"] == "healthy"),
        "degraded": sum(1 for r in results if r["status"] == "degraded"),
        "down": sum(1 for r in results if r["status"] == "down"),
    }
    return {"summary": summary, "results": results}


@app.get("/api/v1/check/{target_name}")
async def check_single(target_name: str):
    target = next((t for t in TARGETS if t["name"] == target_name), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Target '{target_name}' not found")
    result = await check_target(target)
    await persist_result(result)
    return result


@app.get("/api/v1/history")
async def history(limit: int = 100, target: str | None = None):
    async with db_pool.acquire() as conn:
        if target:
            rows = await conn.fetch(
                "SELECT * FROM health_checks WHERE target=$1 ORDER BY checked_at DESC LIMIT $2",
                target, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM health_checks ORDER BY checked_at DESC LIMIT $1", limit
            )
    return {"count": len(rows), "records": [dict(r) for r in rows]}
