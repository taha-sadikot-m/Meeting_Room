"""
LiveKit Meeting Room — Python Backend (server.py)
================================================
FastAPI server that:
  1. Downloads livekit-client.js once and serves it locally (no CDN needed)
  2. Serves the frontend HTML page
  3. Issues signed LiveKit JWT tokens for participants

Run:
    python server.py
Then open  http://localhost:8000  in two different browser tabs.
"""

import os
import time
import datetime
import uuid
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── LiveKit token generation ──────────────────────────────────────────────────
from livekit.api import AccessToken, VideoGrants

load_dotenv()  # reads .env file

LIVEKIT_URL        = os.getenv("LIVEKIT_URL",        "wss://your-project.livekit.cloud")
LIVEKIT_API_KEY    = os.getenv("LIVEKIT_API_KEY",    "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "devsecret")

# ── Download LiveKit JS bundle once, serve it locally ────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
LIVEKIT_JS = STATIC_DIR / "livekit-client.umd.min.js"

LIVEKIT_JS_URLS = [
    "https://unpkg.com/livekit-client/dist/livekit-client.umd.min.js",
    "https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js",
]

if not LIVEKIT_JS.exists():
    print("Downloading livekit-client.js ...")
    downloaded = False
    for url in LIVEKIT_JS_URLS:
        try:
            urllib.request.urlretrieve(url, LIVEKIT_JS)
            print(f"Saved to {LIVEKIT_JS}")
            downloaded = True
            break
        except Exception as e:
            print(f"  Failed {url}: {e}")
    if not downloaded:
        print("WARNING: Could not download livekit-client.js.")
        print("  Manually download from:")
        print("  https://unpkg.com/livekit-client/dist/livekit-client.umd.min.js")
        print(f"  and place it at: {LIVEKIT_JS}")
else:
    print(f"livekit-client.js ready ({LIVEKIT_JS})")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="LiveKit Meeting Room")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve /static/livekit-client.umd.min.js locally
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Request / Response models ─────────────────────────────────────────────────
class TokenRequest(BaseModel):
    room_name: str
    participant_name: str


class TokenResponse(BaseModel):
    token: str
    livekit_url: str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the single-file meeting room UI."""
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/token", response_model=TokenResponse)
async def get_token(req: TokenRequest):
    """
    Generate a signed LiveKit access token.

    The token grants the participant permission to:
      * publish and subscribe to audio/video tracks
      * join the requested room (created automatically if it doesn't exist)
    """
    if not req.room_name.strip():
        raise HTTPException(status_code=400, detail="room_name cannot be empty")
    if not req.participant_name.strip():
        raise HTTPException(status_code=400, detail="participant_name cannot be empty")

    display_name = req.participant_name.strip()
    # Keep display names human-friendly while ensuring unique identities in-room.
    unique_identity = f"{display_name}-{uuid.uuid4().hex[:8]}"

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
        .with_ttl(datetime.timedelta(hours=1))
        .to_jwt()
    )

    return TokenResponse(token=token, livekit_url=LIVEKIT_URL)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "timestamp": time.time()})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 55)
    print("  LiveKit Meeting Room Server")
    print("=" * 55)
    print(f"  URL      : http://localhost:8000")
    print(f"  LiveKit  : {LIVEKIT_URL}")
    print("=" * 55)
    print("  Open http://localhost:8000 in TWO browser tabs")
    print("  Use the SAME room name, different usernames")
    print("=" * 55 + "\n")

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)