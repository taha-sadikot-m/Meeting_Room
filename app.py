"""
MeetKit — Python Backend (server.py)
=====================================
Production-grade FastAPI server that:
  1. Downloads livekit-client.js once and serves it locally
  2. Serves the frontend HTML
  3. Issues signed LiveKit JWT tokens
  4. Includes structured logging, request validation, health & readiness checks

Run:
    python server.py
Then open  http://localhost:8080  in two browser tabs.

Environment variables (via .env):
    LIVEKIT_URL         wss://your-project.livekit.cloud
    LIVEKIT_API_KEY     your_api_key
    LIVEKIT_API_SECRET  your_api_secret
    PORT                8080  (optional)
    LOG_LEVEL           info  (optional: debug | info | warning | error)
    TOKEN_TTL_HOURS     1     (optional, default 1)
    ALLOWED_ORIGINS     *     (optional, comma-separated list for strict CORS)
"""

import os
import sys
import time
import datetime
import uuid
import logging
import urllib.request
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

# ── LiveKit token generation ───────────────────────────────────────────────────
from livekit.api import AccessToken, VideoGrants

# ── Logging setup ──────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("meetkit")

# ── Load environment ───────────────────────────────────────────────────────────
load_dotenv()

LIVEKIT_URL        = os.getenv("LIVEKIT_URL",        "wss://your-project.livekit.cloud")
LIVEKIT_API_KEY    = os.getenv("LIVEKIT_API_KEY",    "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "devsecret")
TOKEN_TTL_HOURS    = int(os.getenv("TOKEN_TTL_HOURS", "1"))
PORT               = int(os.getenv("PORT", "8080"))

# Warn on obvious dev defaults in a production-looking setup
if LIVEKIT_API_KEY == "devkey" or LIVEKIT_API_SECRET == "devsecret":
    logger.warning(
        "Using default API credentials. Set LIVEKIT_API_KEY and "
        "LIVEKIT_API_SECRET in your .env file before going to production."
    )

# ── CORS origins ───────────────────────────────────────────────────────────────
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS: list[str] = (
    ["*"] if _raw_origins.strip() == "*"
    else [o.strip() for o in _raw_origins.split(",") if o.strip()]
)

# ── Download LiveKit JS once, serve locally ────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
LIVEKIT_JS = STATIC_DIR / "livekit-client.umd.min.js"

LIVEKIT_JS_URLS = [
    "https://unpkg.com/livekit-client/dist/livekit-client.umd.min.js",
    "https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js",
]

if not LIVEKIT_JS.exists():
    logger.info("Downloading livekit-client.js …")
    downloaded = False
    for url in LIVEKIT_JS_URLS:
        try:
            urllib.request.urlretrieve(url, LIVEKIT_JS)
            logger.info("Saved → %s", LIVEKIT_JS)
            downloaded = True
            break
        except Exception as exc:
            logger.warning("Failed %s: %s", url, exc)
    if not downloaded:
        logger.error(
            "Could not download livekit-client.js. "
            "Manually place it at: %s", LIVEKIT_JS
        )
else:
    size_kb = LIVEKIT_JS.stat().st_size // 1024
    logger.info("livekit-client.js ready (%d KB)", size_kb)

# ── FastAPI application ────────────────────────────────────────────────────────
app = FastAPI(
    title="MeetKit",
    description="Production LiveKit meeting room server",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=ALLOWED_ORIGINS != ["*"],
)

# Serve /static/* locally (livekit-client.js etc.)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Startup log ───────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    logger.info("MeetKit starting on port %d", PORT)
    logger.info("LiveKit server : %s", LIVEKIT_URL)
    logger.info("CORS origins   : %s", ALLOWED_ORIGINS)
    logger.info("Token TTL      : %d h", TOKEN_TTL_HOURS)

# ── Request / Response models ──────────────────────────────────────────────────
class TokenRequest(BaseModel):
    room_name: str = Field(..., min_length=1, max_length=128)
    participant_name: str = Field(..., min_length=1, max_length=64)
    room_id: Optional[str] = Field(None, min_length=1, max_length=36)

    @field_validator("room_name", "participant_name", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

    @field_validator("room_name")
    @classmethod
    def sanitise_room_name(cls, v: str) -> str:
        # Allow alphanumeric, hyphens, underscores, dots, spaces
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. ")
        if not all(c in allowed for c in v):
            raise ValueError("Room name may only contain letters, numbers, hyphens, underscores, dots, and spaces.")
        return v


class TokenResponse(BaseModel):
    token: str
    livekit_url: str


class RoomCreateRequest(BaseModel):
    room_name: str = Field(..., min_length=1, max_length=128)
    
    @field_validator("room_name", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

    @field_validator("room_name")
    @classmethod
    def sanitise_room_name(cls, v: str) -> str:
        # Allow alphanumeric, hyphens, underscores, dots, spaces
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. ")
        if not all(c in allowed for c in v):
            raise ValueError("Room name may only contain letters, numbers, hyphens, underscores, dots, and spaces.")
        return v


class RoomCreateResponse(BaseModel):
    room_id: str
    room_name: str
    meeting_url: str


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    """Serve the single-file meeting room UI."""
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        logger.error("index.html not found at %s", html_path)
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/create-room", response_model=RoomCreateResponse)
async def create_room(req: RoomCreateRequest, request: Request):
    """
    Create a new meeting room with a unique ID.
    
    Returns:
      - room_id: Unique identifier for the room (UUID)
      - room_name: The name of the room
      - meeting_url: Shareable URL to join the room
    """
    client_ip = request.client.host if request.client else "unknown"
    room_id = str(uuid.uuid4())
    
    logger.info(
        "Room creation request — room_name=%r  room_id=%r  ip=%s",
        req.room_name, room_id, client_ip,
    )
    
    # Construct the shareable URL
    # Format: http://localhost:8080/?room=<room_name>&id=<room_id>
    protocol = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", request.client.host if request.client else "localhost:8080")
    meeting_url = f"{protocol}://{host}/?room={req.room_name}&id={room_id}"
    
    logger.debug("Created room — url=%s", meeting_url)
    return RoomCreateResponse(
        room_id=room_id,
        room_name=req.room_name,
        meeting_url=meeting_url
    )


@app.post("/token", response_model=TokenResponse)
async def get_token(req: TokenRequest, request: Request):
    """
    Generate a signed LiveKit access token.

    The token grants the participant permission to:
      - join the requested room (auto-created if it doesn't exist)
      - publish & subscribe to audio/video tracks
      - send and receive data messages
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "Token request — room=%r  participant=%r  room_id=%r  ip=%s",
        req.room_name, req.participant_name, req.room_id, client_ip,
    )

    # Unique identity prevents collisions when the same display name joins twice
    display_name     = req.participant_name
    unique_identity  = f"{display_name}-{uuid.uuid4().hex[:8]}"

    try:
        token = (
            AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity(unique_identity)
            .with_name(display_name)
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=req.room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_ttl(datetime.timedelta(hours=TOKEN_TTL_HOURS))
            .to_jwt()
        )
    except Exception as exc:
        logger.exception("Token generation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token generation failed. Check server configuration.",
        )

    logger.debug("Issued token for identity=%r room=%r", unique_identity, req.room_name)
    return TokenResponse(token=token, livekit_url=LIVEKIT_URL)


@app.get("/health", include_in_schema=False)
async def health():
    """Liveness check — returns 200 if the process is alive."""
    return JSONResponse({"status": "ok", "timestamp": time.time()})


@app.get("/ready", include_in_schema=False)
async def ready():
    """
    Readiness check — returns 200 only when the server can issue tokens.
    Returns 503 if credentials look unconfigured.
    """
    issues = []
    if LIVEKIT_API_KEY in ("devkey", ""):
        issues.append("LIVEKIT_API_KEY not configured")
    if LIVEKIT_API_SECRET in ("devsecret", ""):
        issues.append("LIVEKIT_API_SECRET not configured")
    if not LIVEKIT_JS.exists():
        issues.append("livekit-client.js missing")

    if issues:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "issues": issues, "timestamp": time.time()},
        )

    return JSONResponse({"status": "ready", "timestamp": time.time()})


# ── Global error handler ──────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    banner = f"""
╔══════════════════════════════════════════════╗
║          MeetKit  —  Meeting Server          ║
╠══════════════════════════════════════════════╣
║  URL      :  http://localhost:{PORT:<15}║
║  LiveKit  :  {LIVEKIT_URL[:32]:<32}║
║  Docs     :  http://localhost:{PORT}/docs    ║
╚══════════════════════════════════════════════╝
  → Open http://localhost:{PORT} in TWO browser tabs
  → Use the SAME room name, different usernames
"""
    print(banner)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        reload=False,
        access_log=True,
        log_level=LOG_LEVEL.lower(),
    )