# ============================================================
# VIDEO GENERATOR — Single Kaggle Cell
# ============================================================
import subprocess, sys, os, asyncio
from pathlib import Path

BASE = Path("/kaggle/working")
REPO = "https://github.com/yogagurjar/movi2.git"

# Clone repo
if not (BASE / "movi2_repo").exists():
    subprocess.run(["git", "clone", REPO, str(BASE / "movi2_repo")], check=True)

# Real project is inside movi2_repo/project/
PROJ = BASE / "movi2_repo" / "project"
os.chdir(str(PROJ))
sys.path.insert(0, str(PROJ))

# Install dependencies
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
    "fastapi", "uvicorn", "python-multipart", "gdown",
    "openai-whisper", "pyngrok", "nest-asyncio"], check=True)
subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
print("Dependencies OK")

# Start server
import uvicorn, nest_asyncio
from pyngrok import ngrok

# ═════════════════════════════════════════════════════════════
# >>>  PASTE YOUR NGROK AUTH TOKEN HERE  <<<
# ═════════════════════════════════════════════════════════════
NGROK_AUTH_TOKEN = ""

if NGROK_AUTH_TOKEN:
    ngrok.set_auth_token(NGROK_AUTH_TOKEN)

nest_asyncio.apply()
tunnel = ngrok.connect(8000)
print("=" * 60)
print(f"  PUBLIC URL: {tunnel.public_url}")
print("=" * 60)

config = uvicorn.Config("app:app", host="0.0.0.0", port=8000,
                        timeout_keep_alive=1200, log_level="info")
server = uvicorn.Server(config)
await server.serve()
