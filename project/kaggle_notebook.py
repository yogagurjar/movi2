"""
==========================================================================
  Video Generator — Kaggle Notebook Setup
==========================================================================
  Copy & paste each cell below into a separate Kaggle Notebook cell.
==========================================================================

Cell 1 — Install dependencies
──────────────────────────────────────────────────────────────────────────
"""
cell1 = '''
!pip install -q fastapi uvicorn python-multipart gdown openai-whisper pyngrok nest-asyncio

# Verify FFmpeg is available
import subprocess, sys
try:
    subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    print("FFmpeg OK")
except Exception:
    print("FFmpeg missing — Kaggle should have it pre-installed")
    sys.exit(1)
'''

"""
Cell 2 — Create project structure & write all source files
──────────────────────────────────────────────────────────────────────────
"""
cell2 = '''
import os, json, shutil
from pathlib import Path

BASE = Path("/kaggle/working/project")
for d in ["frontend", "downloads", "transcript", "scene_json", "screenshots", "output", "temp"]:
    (BASE / d).mkdir(parents=True, exist_ok=True)

# ── app.py ─────────────────────────────────────────────────────────────────
(BASE / "app.py").write_text(r"""__APP_PY_PLACEHOLDER__""")

# ── index.html ─────────────────────────────────────────────────────────────
(BASE / "frontend" / "index.html").write_text(r"""__INDEX_HTML_PLACEHOLDER__""")

# ── style.css ──────────────────────────────────────────────────────────────
(BASE / "frontend" / "style.css").write_text(r"""__STYLE_CSS_PLACEHOLDER__""")

# ── script.js ──────────────────────────────────────────────────────────────
(BASE / "frontend" / "script.js").write_text(r"""__SCRIPT_JS_PLACEHOLDER__""")

print("All project files created under", BASE)
'''

"""
Cell 3 — Set Ngrok auth token (REQUIRED)
──────────────────────────────────────────────────────────────────────────
 1. Sign up at https://ngrok.com  (free)
 2. Get your auth token from https://dashboard.ngrok.com/get-started/your-authtoken
 3. Paste it below
"""
cell3 = '''
from pyngrok import ngrok
import nest_asyncio

# ═══════════════════════════════════════════════════════════════════════
# >>>  PASTE YOUR NGROK AUTH TOKEN HERE  <<<
# ═══════════════════════════════════════════════════════════════════════
NGROK_AUTH_TOKEN = ""

if NGROK_AUTH_TOKEN:
    ngrok.set_auth_token(NGROK_AUTH_TOKEN)
    print("Ngrok auth token configured.")
else:
    print("WARNING: No ngrok token set. Run this cell again after pasting your token.")
'''

"""
Cell 4 — Run the application
──────────────────────────────────────────────────────────────────────────
"""
cell4 = '''
import sys, os
sys.path.insert(0, "/kaggle/working/project")
os.chdir("/kaggle/working/project")

from pyngrok import ngrok
import uvicorn
import nest_asyncio

# Start ngrok tunnel
tunnel = ngrok.connect(8000)
PUBLIC_URL = tunnel.public_url

print("=" * 60)
print(f"  PUBLIC URL: {PUBLIC_URL}")
print("=" * 60)
print()
print("Open the URL above in your browser to use the application.")
print("Press STOP (■) in the notebook toolbar to shut down.")

nest_asyncio.apply()
uvicorn.run(
    "app:app",
    host="0.0.0.0",
    port=8000,
    timeout_keep_alive=1200,
    log_level="info",
)
'''

# ── Actually write the real source files to project/ ──────────────────
SOURCE = Path(__file__).parent.resolve()

def embed_file(path: str) -> str:
    """Read a source file and return its content, escaped for embedding."""
    full = SOURCE / path
    if not full.exists():
        return f"# FILE NOT FOUND: {path}"
    return full.read_text(encoding="utf-8")


app_py = embed_file("app.py")
index_html = embed_file("frontend/index.html")
style_css = embed_file("frontend/style.css")
script_js = embed_file("frontend/script.js")

# Build the self-contained notebook cell that writes all files
cell2_actual = cell2.replace('__APP_PY_PLACEHOLDER__', app_py)
cell2_actual = cell2_actual.replace('__INDEX_HTML_PLACEHOLDER__', index_html)
cell2_actual = cell2_actual.replace('__STYLE_CSS_PLACEHOLDER__', style_css)
cell2_actual = cell2_actual.replace('__SCRIPT_JS_PLACEHOLDER__', script_js)

# ── Write the Kaggle notebook instructions ────────────────────────────
output_path = SOURCE / "KAGGLE_SETUP.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write("=" * 72 + "\n")
    f.write("  VIDEO GENERATOR — KAGGLE NOTEBOOK SETUP\n")
    f.write("=" * 72 + "\n\n")

    parts = [
        ("CELL 1 — Install dependencies", cell1.strip()),
        ("CELL 2 — Create project & write source files", cell2_actual.strip()),
        ("CELL 3 — Set Ngrok auth token", cell3.strip()),
        ("CELL 4 — Run the application", cell4.strip()),
    ]

    for i, (title, code) in enumerate(parts, 1):
        f.write(f"─" * 72 + "\n")
        f.write(f"  {title}\n")
        f.write(f"─" * 72 + "\n\n")
        f.write(code)
        f.write("\n\n")

    f.write("=" * 72 + "\n")
    f.write("  END OF NOTEBOOK SETUP\n")
    f.write("=" * 72 + "\n")

print(f"Kaggle setup written to: {output_path}")
print("Files embedded successfully:")
print(f"  app.py        {len(app_py):>7} chars")
print(f"  index.html    {len(index_html):>7} chars")
print(f"  style.css     {len(style_css):>7} chars")
print(f"  script.js     {len(script_js):>7} chars")
