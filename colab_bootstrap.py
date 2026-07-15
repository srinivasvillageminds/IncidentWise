# -*- coding: utf-8 -*-
"""IncidentWise — Colab demo bootstrap (branch: colab-demo).

From a fresh GPU Colab to a public UI link in one run:
  1. mounts Drive, clones this repo (GH_PAT from Colab secrets if private)
  2. restores all non-git state from  MyDrive/safety_gpt_restore/  into the
     right locations (corpus, chroma_db, OCR cache, incidents.json, history…)
  3. installs deps + Ollama, starts it as a subprocess (GPU auto-detected)
  4. pulls the embed + chat models, wires OPENAI_API_KEY from Colab secrets
  5. starts uvicorn, opens a Cloudflare quick tunnel, prints your links

Usage (Colab, GPU runtime):   %run colab_bootstrap.py
(Use %run, not !python — Colab secrets and the proxy link need the kernel.)

Helpers after it's up:
  backup_to_drive()   push current state back to MyDrive/safety_gpt_restore
  stop_all()          stop uvicorn + ollama + tunnel

Expected Drive layout (all optional — whatever exists gets restored):
  safety_gpt_restore/
    crawler_data/            -> safety-pdf-crawler/data   (raw_pdfs + manifests)
    corpus.zip               -> (alternative) unzipped into safety-pdf-crawler/
    sgpt_artifacts.zip       -> (alternative) unzipped into incidentwise/
    chroma_db/  ocr_cache/  drill_library/  sample_permits/
    triage_cache.json  facts_cache.json  incidents.json  ingest_report.json
    history.db  jobs_log.json  plant_model.json
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ----------------------------- CONFIG ---------------------------------------
GH_USER_REPO = "srinivasvillageminds/incidentwise"
BRANCH       = "publish"             # public release branch (falls back to default if missing)
CLONE_DIR    = Path("/content/incidentwise")
RESTORE_DIR  = Path("/content/drive/MyDrive/safety_gpt_restore")
LEGACY_CRAWL = Path("/content/drive/MyDrive/safety_pdf_crawler_data")  # old layout
PORT         = 8000
CHAT_BACKEND = "ollama"              # "ollama" | "openai"  (dropdown can override per question)
CHAT_MODEL   = "llama3.1:8b"
EMBED_MODEL  = "nomic-embed-text"
PULL_VLM     = False                 # True only if you plan to OCR in this session
PULL_EXTRAS  = True                  # test-bench models (~25 GB total, ~15 min first time)
# Sub-14B field for THIS workload (instruction-following = the guardrail layer;
# JSON mode = drills/facts/guards; narrative coherence = drills). Needs ~16 GB VRAM
# to run any ONE of these at a time - models are loaded/unloaded per call, so a
# 22 GB GPU handles the whole sweep comfortably.
#   qwen2.5:14b    the ceiling under 14B; best local narrative coherence
#   phi4:14b       Microsoft, strong reasoning + JSON discipline
#   gemma3:12b     excellent instruction follower; drill-quality candidate
#   qwen2.5:7b     best-in-class instruction following at 7B, 128K ctx
#   granite3.3:8b  IBM RAG/business-tuned: stays in context, cites rather than embroiders
#   gemma3:4b      the "how cheap can we go" floor for the bench story
# llama3.1:8b (CHAT_MODEL above) stays as the control group.
EXTRA_OLLAMA_MODELS = ["qwen2.5:14b", "phi4:14b", "gemma3:12b",
                       "qwen2.5:7b", "granite3.3:8b", "gemma3:4b"]
# NOTE: Grok/xAI is NOT an Ollama model - it appears in the UI dropdown and
# bench automatically when the XAI_API_KEY Colab secret exists (wired below).
# -----------------------------------------------------------------------------

APP = CLONE_DIR / "incidentwise"
CRAWLER = CLONE_DIR / "safety-pdf-crawler"
_procs: dict = {}


def sh(cmd, **kw):
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, check=False, **kw)


def step(msg):
    print(f"\n{'='*70}\n>> {msg}\n{'='*70}")


# --- 1. Drive + clone ----------------------------------------------------------
def mount_and_clone():
    step("Mounting Drive & cloning repo")
    from google.colab import drive, userdata
    drive.mount("/content/drive")

    token = ""
    try:
        token = userdata.get("GH_PAT") or ""
    except Exception:
        pass
    url = (f"https://{token}@github.com/{GH_USER_REPO}.git" if token
           else f"https://github.com/{GH_USER_REPO}.git")

    if CLONE_DIR.exists():
        sh(f"cd {CLONE_DIR} && git fetch --all -q && git pull -q")
    else:
        r = sh(f"git clone -q -b {BRANCH} {url} {CLONE_DIR}")
        if r.returncode != 0:
            print(f"branch '{BRANCH}' not found - cloning default branch")
            sh(f"git clone -q {url} {CLONE_DIR}")
    assert APP.exists(), "clone failed - check GH_PAT secret / repo name"


# --- 2. restore state from Drive ------------------------------------------------
def _copy(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return True


def restore_state():
    step(f"Restoring non-git state from {RESTORE_DIR}")
    found, missing = [], []

    # corpus (folder, zip, or legacy Drive folder - first match wins)
    if _copy(RESTORE_DIR / "crawler_data", CRAWLER / "data"):
        found.append("crawler_data/ -> safety-pdf-crawler/data")
    elif (RESTORE_DIR / "corpus.zip").exists():
        sh(f"unzip -oq '{RESTORE_DIR / 'corpus.zip'}' -d '{CRAWLER}'")
        found.append("corpus.zip -> safety-pdf-crawler/")
    elif _copy(LEGACY_CRAWL, CRAWLER / "data"):
        found.append(f"(legacy) {LEGACY_CRAWL.name}/ -> safety-pdf-crawler/data")
    else:
        missing.append("corpus (crawler_data/ or corpus.zip)")

    # app artifacts zip (your existing sgpt_artifacts.zip works as-is)
    if (RESTORE_DIR / "sgpt_artifacts.zip").exists():
        sh(f"unzip -oq '{RESTORE_DIR / 'sgpt_artifacts.zip'}' -d '{APP}'")
        found.append("sgpt_artifacts.zip -> incidentwise/")

    items = ["chroma_db", "ocr_cache", "drill_library", "sample_permits",
             "triage_cache.json", "facts_cache.json", "incidents.json",
             "ingest_report.json", "history.db", "jobs_log.json", "plant_model.json"]
    for name in items:
        if _copy(RESTORE_DIR / name, APP / name):
            found.append(name)
        elif not (APP / name).exists():
            missing.append(name)

    print("\nrestored:", *[f"  + {f}" for f in found], sep="\n")
    if missing:
        print("not found (ok if intentional):", *[f"  - {m}" for m in missing], sep="\n")
    if not (APP / "chroma_db").exists():
        print("\n!! No chroma_db restored -> the index is EMPTY. Either add it to "
              "safety_gpt_restore or run:  !cd /content/incidentwise/incidentwise && python ingest.py")


# --- 3. deps + ollama -------------------------------------------------------------
def _find_ollama() -> str | None:
    """PATH lookup + known install locations (Popen doesn't read shell rc files)."""
    p = shutil.which("ollama")
    if p:
        return p
    for cand in ("/usr/local/bin/ollama", "/usr/bin/ollama", "/opt/ollama/bin/ollama"):
        if Path(cand).exists():
            return cand
    return None


def install_and_start_ollama():
    step("Installing dependencies + Ollama (GPU auto-detected)")
    sh(f"pip install -q -r {APP / 'requirements.txt'}")
    gpu = sh("nvidia-smi --query-gpu=name --format=csv,noheader",
             capture_output=True, text=True)
    print("GPU:", (gpu.stdout or "").strip() or "NONE - Runtime > Change runtime type > GPU")

    binary = _find_ollama()
    if binary is None:
        # Ollama's installer needs zstd for extraction; pciutils/lshw help its
        # GPU detection. Colab's image ships none of them.
        sh("apt-get install -y -q zstd pciutils lshw")
        r = sh("curl -fsSL https://ollama.com/install.sh | sh",
               capture_output=True, text=True)
        print((r.stdout or "")[-1200:])
        print((r.stderr or "")[-1200:])
        binary = _find_ollama()
    if binary is None:
        raise RuntimeError(
            "Ollama binary not found after install (see installer output above).\n"
            "Manual fallback in a cell:\n"
            "  !curl -fsSL https://ollama.com/install.sh | sh\n"
            "  !which ollama\n"
            "then re-run:  %run colab_bootstrap.py")
    os.environ["PATH"] = os.environ.get("PATH", "") + ":" + str(Path(binary).parent)
    print("ollama binary:", binary)

    sh("pkill -f 'ollama serve' 2>/dev/null; sleep 1")  # stale process from a prior attempt
    log = open("/content/ollama_server.log", "w")
    _procs["ollama"] = subprocess.Popen([binary, "serve"], stdout=log, stderr=log)

    import httpx
    up = False
    for _ in range(30):
        try:
            httpx.get("http://localhost:11434/api/tags", timeout=2)
            up = True
            break
        except Exception:
            time.sleep(1)
    if not up:
        sh("tail -n 30 /content/ollama_server.log")
        raise RuntimeError("ollama serve did not come up - log above")
    print("Ollama up. Pulling models (cached across session)...")
    sh(f"{binary} pull {EMBED_MODEL}")
    sh(f"{binary} pull {CHAT_MODEL}")
    if PULL_EXTRAS:
        for m in EXTRA_OLLAMA_MODELS:
            sh(f"{binary} pull {m}")
    if PULL_VLM:
        sh(f"{binary} pull qwen3-vl:8b")


# --- 4. env + server + tunnel -------------------------------------------------------
def start_server():
    step("Starting IncidentWise server")
    os.environ["CHAT_BACKEND"] = CHAT_BACKEND
    os.environ["OLLAMA_MODEL"] = CHAT_MODEL
    os.environ["OLLAMA_EMBED_MODEL"] = EMBED_MODEL
    for secret in ("OPENAI_API_KEY", "XAI_API_KEY"):
        try:
            from google.colab import userdata
            key = userdata.get(secret)
            if key:
                os.environ[secret] = key
                print(f"{secret} wired from Colab secrets (never printed).")
        except Exception:
            print(f"No {secret} secret - that provider disabled.")

    sh("pkill -f 'uvicorn app:app' 2>/dev/null; sleep 1")  # stale from prior attempt
    log = open("/content/uvicorn.log", "w")
    _procs["uvicorn"] = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app",
         "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=str(APP), stdout=log, stderr=log, env=os.environ.copy())

    import httpx
    health = {}
    for _ in range(60):
        try:
            health = httpx.get(f"http://localhost:{PORT}/api/health", timeout=2).json()
            break
        except Exception:
            time.sleep(1)
    print("health:", json.dumps({k: health.get(k) for k in ("ok", "index_chunks", "chat")}))
    if not health:
        sh("tail -n 30 /content/uvicorn.log")
        raise RuntimeError("server did not come up - see uvicorn.log above")


def open_tunnel() -> str:
    step("Opening public tunnel (Cloudflare, no signup)")
    cf = Path("/content/cloudflared")
    if not cf.exists():
        urllib.request.urlretrieve(
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
            cf)
        cf.chmod(0o755)
    sh("pkill -f cloudflared 2>/dev/null; sleep 1")  # stale tunnel from prior attempt
    log_path = "/content/cloudflared.log"
    log = open(log_path, "w")
    _procs["tunnel"] = subprocess.Popen(
        [str(cf), "tunnel", "--url", f"http://localhost:{PORT}", "--no-autoupdate"],
        stdout=log, stderr=log)
    url = ""
    for _ in range(30):
        time.sleep(1)
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",
                      Path(log_path).read_text(errors="replace"))
        if m:
            url = m.group(0)
            break
    return url


# --- helpers you can call later -----------------------------------------------------
def backup_to_drive():
    """Push current session state back to MyDrive/safety_gpt_restore."""
    RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in ["chroma_db", "ocr_cache", "drill_library", "sample_permits",
                 "triage_cache.json", "facts_cache.json", "incidents.json",
                 "ingest_report.json", "history.db", "jobs_log.json", "plant_model.json"]:
        if _copy(APP / name, RESTORE_DIR / name):
            n += 1
    if (CRAWLER / "data").exists():
        _copy(CRAWLER / "data", RESTORE_DIR / "crawler_data")
        n += 1
    print(f"backed up {n} item(s) -> {RESTORE_DIR}")


def stop_all():
    for name, p in _procs.items():
        try:
            p.terminate()
            print("stopped", name)
        except Exception:
            pass


# --- main ------------------------------------------------------------------------------
def main():
    mount_and_clone()
    restore_state()
    install_and_start_ollama()
    start_server()
    public = open_tunnel()

    step("READY")
    try:
        from google.colab.output import eval_js
        proxy = eval_js(f"google.colab.kernel.proxyPort({PORT})")
        print(f"Private link (you only):  {proxy}")
    except Exception:
        pass
    if public:
        print(f"PUBLIC demo link:         {public}")
        print(f"Classic UI fallback:      {public}/classic.html")
    else:
        print("Tunnel URL not detected - check /content/cloudflared.log")
    print("\nNotes: links live only while this Colab session runs. Anyone with the "
          "public link can use your session - share deliberately, close when done "
          "(stop_all()). Save new state back to Drive with backup_to_drive().")


if __name__ == "__main_