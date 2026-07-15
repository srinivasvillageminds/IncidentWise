"""Sync local non-git state -> Google Drive 'safety_gpt_restore' folder.

Routes, in order of convenience:
  A. Google Drive for Desktop installed  -> syncs DIRECTLY into your Drive
     folder (auto-detected, or pass --dest). Incremental: unchanged files skip.
  B. --stage                             -> builds  _restore_staging\  next to
     this script (+ optional --zip) for manual browser upload.
  C. rclone users                        -> stage, then:
     rclone sync _restore_staging gdrive:safety_gpt_restore --progress

Never touches .env. Safe to re-run any time (mtime+size incremental).

Usage:
  python backup_to_restore.py                      # auto-detect Drive Desktop
  python backup_to_restore.py --dest "G:\\My Drive\\safety_gpt_restore"
  python backup_to_restore.py --stage [--zip]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "incidentwise"
CRAWLER = ROOT / "safety-pdf-crawler"

# (source, name-in-restore-folder)
ITEMS: list[tuple[Path, str]] = [
    (APP / "chroma_db", "chroma_db"),
    (APP / "ocr_cache", "ocr_cache"),
    (APP / "drill_library", "drill_library"),
    (APP / "sample_permits", "sample_permits"),
    (APP / "triage_cache.json", "triage_cache.json"),
    (APP / "facts_cache.json", "facts_cache.json"),
    (APP / "incidents.json", "incidents.json"),
    (APP / "ingest_report.json", "ingest_report.json"),
    (APP / "history.db", "history.db"),
    (APP / "jobs_log.json", "jobs_log.json"),
    (APP / "plant_model.json", "plant_model.json"),
    (CRAWLER / "data", "crawler_data"),
]

DRIVE_CANDIDATES = [
    Path(r"G:\My Drive"), Path(r"H:\My Drive"), Path(r"I:\My Drive"),
    Path(os.path.expanduser("~")) / "Google Drive" / "My Drive",
    Path(os.path.expanduser("~")) / "GoogleDrive" / "My Drive",
]


def _sync_file(src: Path, dst: Path) -> bool:
    """Copy if new or changed (size+mtime). Returns True if copied."""
    if dst.exists():
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and int(s.st_mtime) <= int(d.st_mtime):
            return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def sync_item(src: Path, dst: Path) -> tuple[int, int, int]:
    """(files_copied, files_skipped, bytes_copied)"""
    copied = skipped = size = 0
    if src.is_file():
        if _sync_file(src, dst):
            copied, size = 1, src.stat().st_size
        else:
            skipped = 1
        return copied, skipped, size
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        if _sync_file(f, dst / rel):
            copied += 1
            size += f.stat().st_size
        else:
            skipped += 1
    return copied, skipped, size


def detect_drive() -> Path | None:
    for c in DRIVE_CANDIDATES:
        if c.exists():
            return c / "safety_gpt_restore"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync state to safety_gpt_restore")
    ap.add_argument("--dest", type=Path, default=None,
                    help="Target folder (e.g. your Drive Desktop path)")
    ap.add_argument("--stage", action="store_true",
                    help="Build local _restore_staging/ instead of syncing to Drive")
    ap.add_argument("--zip", action="store_true",
                    help="With --stage: also produce safety_gpt_restore.zip")
    args = ap.parse_args()

    if args.stage:
        dest = ROOT / "_restore_staging"
    else:
        dest = args.dest or detect_drive()
        if dest is None:
            print("Google Drive for Desktop not detected.\n"
                  "Options:\n"
                  "  1) install Drive for Desktop, re-run (fully automatic)\n"
                  "  2) python backup_to_restore.py --stage   then upload the "
                  "_restore_staging folder (or --zip) to MyDrive/safety_gpt_restore\n"
                  "  3) pass --dest \"<your drive path>\\safety_gpt_restore\"")
            return 1

    print(f"Target: {dest}\n")
    total_c = total_s = total_b = 0
    for src, name in ITEMS:
        if not src.exists():
            print(f"  --      {name:<24} (not present locally, skipped)")
            continue
        c, s, b = sync_item(src, dest / name)
        total_c += c; total_s += s; total_b += b
        print(f"  {'SYNCED' if c else 'ok    '}  {name:<24} "
              f"{c} copied, {s} unchanged, {b/1e6:.1f} MB new")

    print(f"\nDone: {total_c} files copied ({total_b/1e6:.1f} MB), "
          f"{total_s} unchanged.")

    if args.stage and args.zip:
        zpath = ROOT / "safety_gpt_restore.zip"
        print(f"Zipping -> {zpath} ...")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in dest.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(dest))
        print(f"Upload {zpath.name} contents into MyDrive/safety_gpt_restore/")
    elif args.stage:
        print(f"Now upload the CONTENTS of {dest} into MyDrive/safety_gpt_restore/")
    else:
        print("Drive Desktop will finish uploading in the background "
              "(check the Drive icon in your system tray before starting Colab).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
