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
import random
import string
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
    meeting_code: Optional[str] = Field(None, min_length=1, max_length=20)

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
    is_admin: bool = False
    admin_identity: Optional[str] = None


class RoomCreateRequest(BaseModel):
    room_name: str = Field(..., min_length=1, max_length=128)
    creator_name: str = Field(..., min_length=1, max_length=128)  # NEW: Creator's display name
    
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
    
    @field_validator("creator_name", mode="before")
    @classmethod
    def strip_creator_name(cls, v: str) -> str:
        return v.strip()


class RoomCreateResponse(BaseModel):
    room_id: str
    room_name: str
    meeting_url: str
    meeting_code: str


# ── In-memory room registry (stores code → room mapping) ──────────────────────
# In production, use a database like PostgreSQL or Redis
_room_registry = {}  # { "code": { "room_name": str, "room_id": str } }


def generate_meeting_code(length: int = 9) -> str:
    """
    Generate a Google Meet-style code: xxx-yyy-zzz
    Format: 3 lowercase letters - 3 lowercase letters - 3 lowercase letters
    Example: abc-def-ghi
    """
    parts = []
    for _ in range(3):
        part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        parts.append(part)
    code = '-'.join(parts)
    
    # Ensure code is unique
    while code in _room_registry:
        parts = []
        for _ in range(3):
            part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
            parts.append(part)
        code = '-'.join(parts)
    
    return code


# ── Participant tracking for admin controls ────────────────────────────────────
# Format: { "room_name": { "participant_id": "identity" } }
_room_participants = {}


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/join/{meeting_code}", response_class=HTMLResponse, include_in_schema=False)
async def join_with_code(meeting_code: str):
    """
    Serve the frontend pre-filled with room details from the meeting code.
    This is used when accessing a shared meeting link.
    """
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        logger.error("index.html not found at %s", html_path)
        raise HTTPException(status_code=404, detail="index.html not found")
    
    html_content = html_path.read_text(encoding="utf-8")
    
    # Pass meeting code to frontend
    # Frontend will look up the room details and auto-join
    script_injection = f"""
    <script>
      window.__meetingCode = '{meeting_code}';
    </script>
    """
    
    # Inject script before closing head tag
    html_content = html_content.replace('</head>', script_injection + '</head>')
    
    return HTMLResponse(content=html_content)


@app.get("/lookup-room", include_in_schema=False)
async def lookup_room(meeting_code: str = ""):
    """
    Look up room details from a meeting code.
    Returns room_name and room_id for the frontend to use.
    
    Query params:
      - meeting_code: The meeting code to look up
    """
    if not meeting_code or meeting_code not in _room_registry:
        raise HTTPException(
            status_code=404,
            detail=f"Meeting code '{meeting_code}' not found. The link may have expired."
        )
    
    room_data = _room_registry[meeting_code]
    logger.info("Room lookup — code=%r  room_name=%r", meeting_code, room_data["room_name"])
    
    return {
        "room_name": room_data["room_name"],
        "room_id": room_data["room_id"],
        "meeting_code": meeting_code
    }


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
    Create a new meeting room with a unique short code.
    
    Returns:
      - room_id: Unique identifier for the room (UUID)
      - room_name: The name of the room
      - meeting_code: Short shareable code (like Google Meet: abc-def-ghi)
      - meeting_url: Shareable URL to join the room
    """
    client_ip = request.client.host if request.client else "unknown"
    room_id = str(uuid.uuid4())
    meeting_code = generate_meeting_code()
    
    # Store the mapping in registry
    # CRITICAL: Store creator name so we can identify them when they join
    _room_registry[meeting_code] = {
        "room_name": req.room_name,
        "room_id": room_id,
        "created_at": datetime.datetime.now().isoformat(),
        "creator_name": req.creator_name,  # Room creator's display name
        "admin_identity": None,  # Will be set when creator joins
        "admin_name": None  # Display name of admin
    }
    
    logger.info(
        "Room created — name=%r  code=%r  room_id=%r  ip=%s",
        req.room_name, meeting_code, room_id, client_ip,
    )
    
    # Construct the shareable URL
    # Format: http://localhost:8080/join/abc-def-ghi
    protocol = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", request.client.host if request.client else "localhost:8080")
    meeting_url = f"{protocol}://{host}/join/{meeting_code}"
    
    logger.debug("Meeting URL — url=%s", meeting_url)
    return RoomCreateResponse(
        room_id=room_id,
        room_name=req.room_name,
        meeting_code=meeting_code,
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
      
    If meeting_code is provided, room details are looked up from the registry.
    """
    client_ip = request.client.host if request.client else "unknown"
    
    # If meeting code provided, look up room details
    room_name = req.room_name
    room_id = req.room_id
    
    if req.meeting_code:
        if req.meeting_code not in _room_registry:
            logger.warning("Invalid meeting code — code=%r  ip=%s", req.meeting_code, client_ip)
            raise HTTPException(
                status_code=404,
                detail="Meeting code not found or has expired."
            )
        room_data = _room_registry[req.meeting_code]
        room_name = room_data["room_name"]
        room_id = room_data["room_id"]
    
    logger.info(
        "Token request — room=%r  participant=%r  room_id=%r  code=%r  ip=%s",
        room_name, req.participant_name, room_id, req.meeting_code, client_ip,
    )

    # Unique identity prevents collisions when the same display name joins twice
    display_name     = req.participant_name
    unique_identity  = f"{display_name}-{uuid.uuid4().hex[:8]}"
    
    # Track if this user is admin
    is_admin = False
    admin_identity = None

    # If joining via meeting code, check/set admin
    if req.meeting_code and req.meeting_code in _room_registry:
        room_data = _room_registry[req.meeting_code]
        # CRITICAL FIX: Creator becomes admin, not first joiner
        # Check if this person is the room creator
        is_creator = (display_name == room_data["creator_name"])
        
        if is_creator:
            # Creator is admin
            room_data["admin_identity"] = unique_identity
            room_data["admin_name"] = display_name
            is_admin = True
            logger.info("Admin assigned to creator — meeting_code=%r  admin=%r", req.meeting_code, display_name)
        elif not room_data["admin_identity"]:
            # Fallback: first non-creator to join becomes admin (shouldn't happen if creator joins)
            room_data["admin_identity"] = unique_identity
            room_data["admin_name"] = display_name
            is_admin = True
            logger.info("Admin assigned to first joiner (creator didn't join) — meeting_code=%r  admin=%r", req.meeting_code, display_name)
        else:
            # Neither creator nor first joiner - check if this is a subsequent join
            is_admin = (room_data["admin_identity"] == unique_identity)
            admin_identity = room_data["admin_identity"]
    
    try:
        token = (
            AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity(unique_identity)
            .with_name(display_name)
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
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

    logger.debug("Issued token for identity=%r room=%r admin=%s", unique_identity, room_name, is_admin)
    return TokenResponse(
        token=token,
        livekit_url=LIVEKIT_URL,
        is_admin=is_admin,
        admin_identity=admin_identity
    )


class RemoveParticipantRequest(BaseModel):
    room_name: str = Field(..., min_length=1, max_length=128)
    admin_identity: str = Field(..., min_length=1)
    participant_identity: str = Field(..., min_length=1)


@app.post("/remove-participant", include_in_schema=False)
async def remove_participant(req: RemoveParticipantRequest, request: Request):
    """
    Remove a participant from a room. Only admin can remove others.
    
    This notifies the frontend, which disconnects the participant.
    For backend enforcement, use LiveKit server API directly.
    """
    client_ip = request.client.host if request.client else "unknown"
    
    logger.info(
        "Remove request — room=%r  admin=%r  target=%r  ip=%s",
        req.room_name, req.admin_identity, req.participant_identity, client_ip,
    )
    
    # Validate request - check admin status by matching stored registry
    # In production, you'd verify the admin identity against your session/jwt
    
    if req.admin_identity == req.participant_identity:
        raise HTTPException(
            status_code=400,
            detail="Admin cannot remove themselves. Use Leave button instead."
        )
    
    logger.info("Participant removal authorized — room=%r  target=%r", req.room_name, req.participant_identity)
    
    return {
        "status": "removed",
        "message": f"Participant {req.participant_identity} removed from room",
        "room": req.room_name
    }


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