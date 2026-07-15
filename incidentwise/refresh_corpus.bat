@echo off
REM Monthly corpus refresh - safe to run any time; everything is incremental.
REM   crawl:  manifest SHA/URL dedup -> only new or changed documents download
REM   ingest: already-indexed docs skip; triage quarantines scrap; OCR cache
REM           means no page is ever OCR'd twice
REM   facts:  extraction cache -> only new documents get an LLM call
REM Schedule with Windows Task Scheduler (monthly) or run by hand.

cd /d %~dp0..\safety-pdf-crawler
call .venv\Scripts\activate.bat
safety-pdf-crawler --seeds seeds.yaml
call deactivate

cd /d %~dp0
call .venv\Scripts\activate.bat
python ingest.py --ocr ollama
python facts.py
echo.
echo Refresh complete. Restart uvicorn so the BM25 cache rebuilds,
echo then review any new QUARANTINE lines and spot-check new incidents.json rows.
