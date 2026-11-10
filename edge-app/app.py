"""edge-gateway: a tiny FastAPI service simulating the main service of an edge box."""
import logging
import os
import shutil
import time
from logging.handlers import RotatingFileHandler

import yaml
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config/app.yaml")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
LOG_DIR = os.path.join(DATA_DIR, "logs")

# A full disk must not crash the service: logging errors are swallowed.
logging.raiseExceptions = False
os.makedirs(LOG_DIR, exist_ok=True)
log = logging.getLogger("edge-gateway")
log.setLevel(logging.INFO)
_handler = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"), maxBytes=4 * 1024 * 1024, backupCount=3)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)

REQ_DURATION = Histogram(
    "edge_request_duration_seconds", "Request duration", ["path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),  # 0.5 is a boundary: p95 > 500 ms is precise
)
REQUESTS = Counter("edge_requests_total", "Requests", ["path", "status"])
DISK_USED = Gauge("edge_disk_used_ratio", "Used ratio of the /data volume")
DISK_USED.set_function(lambda: (lambda u: u.used / u.total)(shutil.disk_usage(DATA_DIR)))

_cfg = {"mtime": None, "data": {}}
STARTED = time.time()


def config() -> dict:
    """Read the config file, re-parsing only when its mtime changes. Keep the last good one on error."""
    try:
        mtime = os.stat(CONFIG_PATH).st_mtime_ns
        if mtime != _cfg["mtime"]:
            with open(CONFIG_PATH) as f:
                _cfg["data"] = yaml.safe_load(f) or {}
            _cfg["mtime"] = mtime
            log.info("config loaded: %s", _cfg["data"])
    except (OSError, yaml.YAMLError) as e:
        log.error("config read failed, keeping previous: %s", e)
    return _cfg["data"]


app = FastAPI()


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    route = request.scope.get("route")
    path = route.path if route else "other"  # bounded label cardinality
    REQ_DURATION.labels(path).observe(elapsed)
    REQUESTS.labels(path, str(response.status_code)).inc()
    if path != "/metrics":
        log.info("%s %s %d %.1fms", request.method, path, response.status_code, elapsed * 1000)
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def status():
    cfg = config()
    time.sleep(cfg.get("latency_ms", 0) / 1000)  # how a bad config push becomes abnormal latency
    return {"service": cfg.get("service_name", "edge-gateway"), "uptime_s": round(time.time() - STARTED)}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
