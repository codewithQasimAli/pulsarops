import os
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("alert-service")
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
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
MONITOR_SERVICE_URL = os.getenv("MONITOR_SERVICE_URL", "http://monitor-service:8000")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
ALERT_EVAL_INTERVAL = int(os.getenv("ALERT_EVAL_INTERVAL", "60"))

ACTIVE_ALERTS_KEY = "pulsarops:active_alerts"

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
ALERTS_FIRED = Counter(
    "alert_fired_total",
    "Total alerts fired",
    ["target", "severity"],
)
ALERTS_RESOLVED = Counter(
    "alert_resolved_total",
    "Total alerts resolved",
    ["target"],
)
ACTIVE_ALERT_COUNT = Gauge(
    "alert_active_count",
    "Number of currently active alerts",
)

# ---------------------------------------------------------------------------
# Shared clients (initialised in lifespan)
# ---------------------------------------------------------------------------
redis_client: aioredis.Redis | None = None
db_pool: asyncpg.Pool | None = None

CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_events (
    id          BIGSERIAL PRIMARY KEY,
    target      TEXT        NOT NULL,
    severity    TEXT        NOT NULL DEFAULT 'warning',
    message     TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'active',
    fired_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_alert_events_target ON alert_events(target);
CREATE INDEX IF NOT EXISTS ix_alert_events_status ON alert_events(status);
"""


# ---------------------------------------------------------------------------
# Core alert functions
# ---------------------------------------------------------------------------
async def fire_alert(target: str, message: str, severity: str = "warning"):
    now = datetime.now(timezone.utc).isoformat()
    alert_data = {
        "target": target,
        "message": message,
        "severity": severity,
        "status": "active",
        "fired_at": now,
    }

    # Persist to Redis hash
    await redis_client.hset(ACTIVE_ALERTS_KEY, target, json.dumps(alert_data))

    # Persist to PostgreSQL
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alert_events (target, severity, message, status, fired_at)
            VALUES ($1, $2, $3, 'active', $4)
            ON CONFLICT DO NOTHING
            """,
            target, severity, message,
            datetime.fromisoformat(now),
        )

    ALERTS_FIRED.labels(target=target, severity=severity).inc()
    active_count = await redis_client.hlen(ACTIVE_ALERTS_KEY)
    ACTIVE_ALERT_COUNT.set(active_count)

    logger.warning(json.dumps({"event": "alert_fired", "target": target, "severity": severity, "message": message}))

    # Webhook notification — failure is non-fatal
    if WEBHOOK_URL:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(WEBHOOK_URL, json=alert_data)
        except Exception as exc:
            logger.error(f"Webhook delivery failed for {target}: {exc}")


async def resolve_alert(target: str):
    existing = await redis_client.hget(ACTIVE_ALERTS_KEY, target)
    if not existing:
        return False

    # Remove from Redis
    await redis_client.hdel(ACTIVE_ALERTS_KEY, target)

    # Update PostgreSQL — mark most recent active alert as resolved
    now = datetime.now(timezone.utc)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE alert_events
            SET    status = 'resolved', resolved_at = $1
            WHERE  target = $2
              AND  status = 'active'
            """,
            now, target,
        )

    ALERTS_RESOLVED.labels(target=target).inc()
    active_count = await redis_client.hlen(ACTIVE_ALERTS_KEY)
    ACTIVE_ALERT_COUNT.set(active_count)

    logger.info(json.dumps({"event": "alert_resolved", "target": target}))
    return True


# ---------------------------------------------------------------------------
# Evaluation logic — compares monitor results against thresholds
# ---------------------------------------------------------------------------
async def evaluate_monitor_results():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{MONITOR_SERVICE_URL}/api/v1/check")
        if resp.status_code != 200:
            logger.error(f"Monitor service returned {resp.status_code}")
            return

        data = resp.json()
        results = data.get("results", [])

        for result in results:
            target = result.get("target", "unknown")
            status = result.get("status", "unknown")

            if status in ("down", "degraded"):
                msg = (
                    f"{target} is {status}. "
                    f"Latency: {result.get('latency_ms')} ms. "
                    f"Error: {result.get('error') or 'none'}."
                )
                severity = "critical" if status == "down" else "warning"
                await fire_alert(target, msg, severity)
            elif status == "healthy":
                await resolve_alert(target)

        logger.info(f"Evaluation complete — assessed {len(results)} targets")

    except Exception as exc:
        logger.error(f"evaluate_monitor_results error: {exc}")


# ---------------------------------------------------------------------------
# Background evaluation loop
# ---------------------------------------------------------------------------
async def evaluation_loop():
    while True:
        await asyncio.sleep(ALERT_EVAL_INTERVAL)
        await evaluate_monitor_results()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, db_pool

    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10, command_timeout=30)
    async with db_pool.acquire() as conn:
        await conn.execute(CREATE_ALERTS_TABLE)
    logger.info("Database schema ensured")

    # Sync active count gauge from Redis at startup
    active_count = await redis_client.hlen(ACTIVE_ALERTS_KEY)
    ACTIVE_ALERT_COUNT.set(active_count)

    asyncio.create_task(evaluation_loop())
    logger.info(f"Alert evaluation loop started — interval {ALERT_EVAL_INTERVAL}s")
    yield

    await redis_client.aclose()
    await db_pool.close()
    logger.info("Connections closed")


app = FastAPI(title="PulsarOps Alert Service", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    try:
        await redis_client.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "unreachable"
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_status = "ok"
    except Exception:
        db_status = "unreachable"
    return {"service": "alert-service", "status": "ok", "redis": redis_status, "database": db_status}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/v1/alerts")
async def get_all_alerts():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM alert_events ORDER BY fired_at DESC LIMIT 200"
        )
    return {"count": len(rows), "alerts": [dict(r) for r in rows]}


@app.get("/api/v1/alerts/active")
async def get_active_alerts():
    raw = await redis_client.hgetall(ACTIVE_ALERTS_KEY)
    alerts = [json.loads(v) for v in raw.values()]
    return {"count": len(alerts), "alerts": alerts}


@app.get("/api/v1/alerts/evaluate")
async def trigger_evaluation():
    """Manually trigger alert evaluation — useful for testing and on-call runbooks."""
    await evaluate_monitor_results()
    return {"status": "evaluation triggered"}


@app.get("/api/v1/alerts/db-history")
async def db_history(limit: int = 100, target: str | None = None):
    async with db_pool.acquire() as conn:
        if target:
            rows = await conn.fetch(
                "SELECT * FROM alert_events WHERE target=$1 ORDER BY fired_at DESC LIMIT $2",
                target, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM alert_events ORDER BY fired_at DESC LIMIT $1", limit
            )
    return {"count": len(rows), "records": [dict(r) for r in rows]}


@app.delete("/api/v1/alerts/{target_name}")
async def delete_alert(target_name: str):
    resolved = await resolve_alert(target_name)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"No active alert for target '{target_name}'")
    return {"status": "resolved", "target": target_name}
