import os
import json
import shutil
import asyncio
import subprocess
import re
import logging
import zipfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import gdown
import whisper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
DOWNLOADS_DIR = BASE_DIR / "downloads"
TRANSCRIPT_DIR = BASE_DIR / "transcript"
SCENE_JSON_DIR = BASE_DIR / "scene_json"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
FRONTEND_DIR = BASE_DIR / "frontend"

for d in (DOWNLOADS_DIR, TRANSCRIPT_DIR, SCENE_JSON_DIR,
          SCREENSHOTS_DIR, OUTPUT_DIR, TEMP_DIR, FRONTEND_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Progress tracker
# ---------------------------------------------------------------------------
class Progress:
    def __init__(self):
        self.percent = 0
        self.status = "idle"
        self.message = ""
        self.logs: List[str] = []
        self.step = "idle"
        self.transcript_ready = False
        self.video_ready = False
        self.error: Optional[str] = None
        self._event = asyncio.Event()
        self._seq = 0

    def update(self, percent: int, status: str, message: str, step: str):
        self.percent = percent
        self.status = status
        self.message = message
        self.step = step
        self.logs.append(f"[{step}] {message}")
        self._seq += 1
        self._event.set()

    def set_error(self, msg: str):
        self.error = msg
        self.status = "error"
        self.message = msg
        self.logs.append(f"[ERROR] {msg}")
        self._event.set()

    def to_dict(self) -> dict:
        return {
            "percent": self.percent,
            "status": self.status,
            "message": self.message,
            "logs": self.logs[-60:],
            "step": self.step,
            "transcript_ready": self.transcript_ready,
            "video_ready": self.video_ready,
            "error": self.error,
            "_seq": self._seq,
        }


progress = Progress()


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self):
        self.movie_path: Optional[Path] = None
        self.audio_path: Optional[Path] = None
        self.transcript: Optional[List[dict]] = None
        self.transcript_path: Optional[Path] = None
        self.scene_json: Optional[List[dict]] = None
        self.scene_json_path: Optional[Path] = None
        self.total_duration: float = 0.0
        self.video_path: Optional[Path] = None


state = AppState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def clean_temp():
    for d in (TEMP_DIR, SCREENSHOTS_DIR):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)


def file_size_mb(p: Path) -> float:
    return p.stat().st_size / (1024 * 1024)


async def run_cmd(args: List[str], desc: str = "", timeout: int = 3600):
    logger.info(f"Running: {' '.join(args[:8])}...")
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{desc} timed out after {timeout}s")
    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:600]
        raise RuntimeError(f"{desc} failed (code {proc.returncode}): {err}")
    return stdout.decode(errors="replace")


def identify_file_type(path: Path) -> str:
    video_ext = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
    audio_ext = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma"}
    ext = path.suffix.lower()
    if ext in video_ext:
        return "video"
    if ext in audio_ext:
        return "audio"
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            info = json.loads(out.stdout)
            for s in info.get("streams", []):
                if s.get("codec_type") == "video":
                    return "video"
                if s.get("codec_type") == "audio":
                    return "audio"
    except Exception:
        pass
    return "unknown"


def parse_drive_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    folder_pats = [
        r"drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/folderview\?.*id=([a-zA-Z0-9_-]+)",
    ]
    for pat in folder_pats:
        m = re.search(pat, url)
        if m:
            return ("folder", m.group(1))
    file_pats = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/uc\?.*id=([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/open\?.*id=([a-zA-Z0-9_-]+)",
    ]
    for pat in file_pats:
        m = re.search(pat, url)
        if m:
            return ("file", m.group(1))
    return (None, None)


def _identify_and_move(files: List[Path]) -> Dict[str, Path]:
    result = {}
    for f in files:
        if not f.is_file():
            continue
        ftype = identify_file_type(f)
        if ftype == "video" and "movie" not in result:
            dest = DOWNLOADS_DIR / "movie.mp4"
            shutil.move(str(f), str(dest))
            result["movie"] = dest
            logger.info(f"Identified video: {f.name} -> movie.mp4")
        elif ftype == "audio" and "audio" not in result:
            dest = DOWNLOADS_DIR / "voice.mp3"
            shutil.move(str(f), str(dest))
            result["audio"] = dest
            logger.info(f"Identified audio: {f.name} -> voice.mp3")
    return result


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Video Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# SSE progress stream
# ---------------------------------------------------------------------------
@app.get("/progress")
async def sse_progress():
    async def event_gen():
        last_seq = -1
        while True:
            d = progress.to_dict()
            if d["_seq"] != last_seq:
                last_seq = d["_seq"]
                yield f"data: {json.dumps(d)}\n\n"
                if d["status"] in ("completed", "error",):
                    break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /download
# ---------------------------------------------------------------------------
@app.post("/download")
async def download_endpoint(link: str = Form(...)):
    try:
        clean_temp()
        progress.update(5, "initializing", "Initialising download…", "download")

        dtype, fid = parse_drive_url(link)
        if not dtype or not fid:
            progress.set_error("Invalid Google Drive link.")
            raise HTTPException(400, "Invalid Google Drive link.  "
                                     "Use a file or folder sharing link.")

        result: Dict[str, Path] = {}

        # ---- try folder ----
        if dtype == "folder":
            progress.update(10, "downloading", "Downloading folder from Drive…", "download")
            out_dir = TEMP_DIR / "drive_folder"
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                gdown.download_folder(link, output=str(out_dir), quiet=False)
                files = list(out_dir.rglob("*"))
                result = _identify_and_move([f for f in files if f.is_file()])
            except Exception as exc:
                logger.warning(f"Folder download failed, trying file: {exc}")

        # ---- try individual file ----
        if not result:
            progress.update(10, "downloading", "Downloading file from Drive…", "download")
            tmp = TEMP_DIR / "drive_file"
            try:
                gdown.download(str(link), str(tmp), quiet=False, fuzzy=True)
            except Exception:
                gdown.download(f"https://drive.google.com/uc?id={fid}",
                               str(tmp), quiet=False)

            if not tmp.exists() or tmp.stat().st_size == 0:
                progress.set_error("Download failed – empty or missing file.")
                raise HTTPException(400, "Download failed – empty result.")

            # zip → extract
            if tmp.suffix.lower() == ".zip":
                progress.update(25, "extracting", "Extracting zip archive…", "download")
                extract_dir = TEMP_DIR / "drive_extracted"
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(tmp, "r") as zf:
                    zf.extractall(extract_dir)
                tmp.unlink()
                files = list(extract_dir.rglob("*"))
                result = _identify_and_move([f for f in files if f.is_file()])
            else:
                ftype = identify_file_type(tmp)
                if ftype == "video":
                    dest = DOWNLOADS_DIR / "movie.mp4"
                    shutil.move(str(tmp), str(dest))
                    result["movie"] = dest
                elif ftype == "audio":
                    dest = DOWNLOADS_DIR / "voice.mp3"
                    shutil.move(str(tmp), str(dest))
                    result["audio"] = dest
                else:
                    # try to treat it anyway
                    dest = DOWNLOADS_DIR / "movie.mp4"
                    shutil.move(str(tmp), str(dest))
                    result["movie"] = dest
                    logger.warning(f"Unknown type {tmp.suffix}, saved as movie.mp4")

        if "movie" not in result:
            progress.set_error("Movie (video file) not found in download.")
            raise HTTPException(400, "Movie file not found.")
        if "audio" not in result:
            progress.set_error("Voice-over (audio file) not found in download.")
            raise HTTPException(400, "Audio file not found.")

        state.movie_path = result["movie"]
        state.audio_path = result["audio"]

        m_mb = file_size_mb(result["movie"])
        a_mb = file_size_mb(result["audio"])
        progress.update(50, "download_complete",
                        f"Ready — movie {m_mb:.0f} MB, audio {a_mb:.0f} MB",
                        "download")

        return {
            "status": "success",
            "movie_size_mb": round(m_mb, 1),
            "audio_size_mb": round(a_mb, 1),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Download error")
        progress.set_error(str(exc))
        raise HTTPException(500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /transcribe
# ---------------------------------------------------------------------------
@app.post("/transcribe")
async def transcribe_endpoint():
    if not state.audio_path or not state.audio_path.exists():
        progress.set_error("Audio file missing – run /download first.")
        raise HTTPException(400, "Audio not found – download first.")

    try:
        progress.update(55, "loading_whisper",
                        "Loading Whisper Large model (may take a minute)…",
                        "transcribe")
        model = whisper.load_model("large")

        progress.update(65, "transcribing",
                        "Transcribing voice-over with Whisper…", "transcribe")
        result = model.transcribe(str(state.audio_path), language="en")

        segments = []
        for seg in result["segments"]:
            segments.append({
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"].strip(),
            })

        t_path = TRANSCRIPT_DIR / "transcript.json"
        with open(t_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, indent=2)

        state.transcript = segments
        state.transcript_path = t_path
        progress.transcript_ready = True

        progress.update(70, "transcript_ready",
                        f"Transcript ready — {len(segments)} segments. "
                        "Please upload Scene JSON.",
                        "transcribe")
        return {"status": "success", "segments": len(segments)}

    except Exception as exc:
        logger.exception("Transcription error")
        progress.set_error(str(exc))
        raise HTTPException(500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /upload_scene_json
# ---------------------------------------------------------------------------
@app.post("/upload_scene_json")
async def upload_scene_json(file: UploadFile = File(...)):
    try:
        raw = await file.read()
        data = json.loads(raw.decode("utf-8"))

        if not isinstance(data, list):
            raise HTTPException(400, "Scene JSON must be a JSON array.")
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise HTTPException(400, f"Item {i} is not an object.")
            for key in ("voice_start", "voice_end", "movie_time"):
                if key not in item:
                    raise HTTPException(400, f"Item {i} missing '{key}'.")

        s_path = SCENE_JSON_DIR / "scene.json"
        with open(s_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        state.scene_json = data
        state.scene_json_path = s_path
        state.total_duration = sum(it["voice_end"] - it["voice_start"]
                                   for it in data)

        progress.update(75, "scene_json_loaded",
                        f"Scene JSON loaded — {len(data)} scenes, "
                        f"{state.total_duration:.1f}s total.",
                        "scene_json")

        return {"status": "success", "scenes": len(data),
                "total_duration": state.total_duration}

    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Upload scene JSON error")
        progress.set_error(str(exc))
        raise HTTPException(500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /render
# ---------------------------------------------------------------------------
@app.post("/render")
async def render_endpoint():
    if not state.scene_json:
        progress.set_error("Scene JSON not uploaded.")
        raise HTTPException(400, "Upload Scene JSON first.")
    if not state.movie_path or not state.movie_path.exists():
        progress.set_error("Movie file missing.")
        raise HTTPException(400, "Movie not found.")
    if not state.audio_path or not state.audio_path.exists():
        progress.set_error("Audio file missing.")
        raise HTTPException(400, "Audio not found.")

    scenes = state.scene_json
    n = len(scenes)

    try:
        clean_temp()

        # ---- 1. Extract screenshots ----
        progress.update(80, "extracting",
                        f"Extracting {n} screenshots from movie…", "extract")
        for i, sc in enumerate(scenes):
            mt = sc["movie_time"]
            out = SCREENSHOTS_DIR / f"scene{i+1:03d}.png"
            await run_cmd([
                "ffmpeg", "-ss", str(mt), "-i", str(state.movie_path),
                "-frames:v", "1", "-q:v", "2",
                str(out), "-y",
            ], f"screenshot {i+1}/{n}")
            pct = 80 + (i + 1) / n * 8
            progress.update(int(pct),
                            "extracting",
                            f"Screenshot {i+1}/{n} at {mt}s", "extract")

        # ---- 2. Build video segments ----
        progress.update(88, "rendering", "Building video segments…", "render")
        segments: List[Path] = []
        for i, sc in enumerate(scenes):
            dur = sc["voice_end"] - sc["voice_start"]
            shot = SCREENSHOTS_DIR / f"scene{i+1:03d}.png"
            seg = TEMP_DIR / f"seg{i+1:03d}.mp4"
            await run_cmd([
                "ffmpeg", "-loop", "1", "-i", str(shot),
                "-c:v", "libx264", "-t", str(dur),
                "-pix_fmt", "yuv420p", "-r", "30",
                "-vf",
                "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                "-preset", "medium", "-crf", "18",
                str(seg), "-y",
            ], f"segment {i+1}/{n}")
            segments.append(seg)

        # ---- 3. Concat ----
        progress.update(94, "rendering", "Concatenating segments…", "render")
        concat_txt = TEMP_DIR / "concat.txt"
        with open(concat_txt, "w") as f:
            for seg in segments:
                f.write(f"file '{seg}'\n")
        concat_vid = TEMP_DIR / "concat_video.mp4"
        await run_cmd([
            "ffmpeg", "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-c", "copy", "-y", str(concat_vid),
        ], "concat")

        # ---- 4. Mux audio ----
        progress.update(96, "rendering", "Adding voice-over audio…", "render")
        final = OUTPUT_DIR / "final_video.mp4"
        await run_cmd([
            "ffmpeg", "-i", str(concat_vid),
            "-i", str(state.audio_path),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-y", str(final),
        ], "audio mux")

        state.video_path = final
        progress.video_ready = True

        mb = file_size_mb(final)
        progress.update(100, "completed",
                        f"Video ready — {mb:.0f} MB  |  {n} scenes  |  "
                        f"{state.total_duration:.0f}s",
                        "completed")

        return {
            "status": "success",
            "size_mb": round(mb, 1),
            "duration": state.total_duration,
            "scenes": n,
        }

    except Exception as exc:
        logger.exception("Render error")
        progress.set_error(str(exc))
        raise HTTPException(500, detail=str(exc))


# ---------------------------------------------------------------------------
# Download endpoints
# ---------------------------------------------------------------------------
@app.get("/download/video")
async def download_video():
    if not state.video_path or not state.video_path.exists():
        raise HTTPException(404, "Video not found – render first.")
    return FileResponse(str(state.video_path),
                        media_type="video/mp4",
                        filename="final_video.mp4",
                        headers={
                            "Content-Disposition":
                            "attachment; filename=final_video.mp4"})


@app.get("/download/transcript")
async def download_transcript():
    if not state.transcript_path or not state.transcript_path.exists():
        raise HTTPException(404, "Transcript not found.")
    return FileResponse(str(state.transcript_path),
                        media_type="application/json",
                        filename="transcript.json",
                        headers={
                            "Content-Disposition":
                            "attachment; filename=transcript.json"})


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "progress": progress.to_dict()}


# ---------------------------------------------------------------------------
# Frontend (must be last so API routes take priority)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True),
          name="frontend")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    from pyngrok import ngrok
    import nest_asyncio

    tunnel = ngrok.connect(8000)
    print(f"\n{'='*60}")
    print(f"  PUBLIC URL: {tunnel.public_url}")
    print(f"{'='*60}\n")

    nest_asyncio.apply()
    uvicorn.run(app, host="0.0.0.0", port=8000,
                timeout_keep_alive=1200)
