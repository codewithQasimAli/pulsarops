import os
import time
import json
import logging
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("api-gateway")
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
MONITOR_SERVICE_URL = os.getenv("MONITOR_SERVICE_URL", "http://monitor-service:8000")
ALERT_SERVICE_URL = os.getenv("ALERT_SERVICE_URL", "http://alert-service:8000")
CACHE_TTL = int(os.getenv("CACHE_TTL", "30"))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total HTTP requests through the gateway",
    ["method", "path", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
RATE_LIMIT_HITS = Counter(
    "gateway_rate_limit_hits_total",
    "Total requests rejected by rate limiter",
    ["ip"],
)
CACHE_HITS = Counter("gateway_cache_hits_total", "Redis cache hits")
CACHE_MISSES = Counter("gateway_cache_misses_total", "Redis cache misses")

# ---------------------------------------------------------------------------
# Redis client (module-level, initialised in lifespan)
# ---------------------------------------------------------------------------
redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    logger.info("Redis connection pool opened")
    yield
    await redis_client.aclose()
    logger.info("Redis connection pool closed")


app = FastAPI(title="PulsarOps API Gateway", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Middleware — structured request logging + Prometheus instrumentation
# ---------------------------------------------------------------------------
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    REQUEST_COUNT.labels(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
    ).inc()
    REQUEST_LATENCY.labels(method=request.method, path=request.url.path).observe(duration)

    logger.info(
        json.dumps({
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration * 1000, 2),
            "client": request.client.host if request.client else "unknown",
        })
    )
    return response


# ---------------------------------------------------------------------------
# Sliding-window rate limiter (Redis-backed, per IP)
# ---------------------------------------------------------------------------
async def check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    key = f"rate:{ip}"
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {str(now): now})
    pipe.zcard(key)
    pipe.expire(key, RATE_LIMIT_WINDOW)
    results = await pipe.execute()

    count = results[2]
    return count <= RATE_LIMIT_REQUESTS


# ---------------------------------------------------------------------------
# Cache-aside helper
# ---------------------------------------------------------------------------
async def cached_proxy(cache_key: str, upstream_url: str) -> dict:
    cached = await redis_client.get(cache_key)
    if cached:
        CACHE_HITS.inc()
        return json.loads(cached)

    CACHE_MISSES.inc()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(upstream_url)
        resp.raise_for_status()
    data = resp.json()
    await redis_client.setex(cache_key, CACHE_TTL, json.dumps(data))
    return data


# ---------------------------------------------------------------------------
# Rate-limit dependency applied to proxied routes
# ---------------------------------------------------------------------------
async def enforce_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    allowed = await check_rate_limit(ip)
    if not allowed:
        RATE_LIMIT_HITS.labels(ip=ip).inc()
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


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
    return {"service": "api-gateway", "status": "ok", "redis": redis_status}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    return {
        "service": "PulsarOps API Gateway",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
    }


@app.get("/api/v1/monitor/check")
async def proxy_monitor_check(request: Request):
    await enforce_rate_limit(request)
    return await cached_proxy("monitor:check:all", f"{MONITOR_SERVICE_URL}/api/v1/check")


@app.get("/api/v1/monitor/check/{target_name}")
async def proxy_monitor_check_target(target_name: str, request: Request):
    await enforce_rate_limit(request)
    return await cached_proxy(
        f"monitor:check:{target_name}",
        f"{MONITOR_SERVICE_URL}/api/v1/check/{target_name}",
    )


@app.get("/api/v1/monitor/history")
async def proxy_monitor_history(request: Request):
    await enforce_rate_limit(request)
    return await cached_proxy("monitor:history", f"{MONITOR_SERVICE_URL}/api/v1/history")


@app.get("/api/v1/alerts")
async def proxy_alerts(request: Request):
    await enforce_rate_limit(request)
    return await cached_proxy("alerts:all", f"{ALERT_SERVICE_URL}/api/v1/alerts")


@app.get("/api/v1/alerts/active")
async def proxy_alerts_active(request: Request):
    await enforce_rate_limit(request)
    return await cached_proxy("alerts:active", f"{ALERT_SERVICE_URL}/api/v1/alerts/active")
