# Colab demo (branch: `publish`)

Run the full IncidentWise stack on a free Colab GPU and get a shareable UI
link, restoring all non-git state (corpus, index, caches, history) from
your Google Drive.

## One-time setup

1. **Drive folder** — create `MyDrive/safety_gpt_restore/` and put your
   state there. Fastest path: run `python backup_to_restore.py` locally
   (auto-syncs via Drive for Desktop) or `--stage` + upload. The bootstrap
   also understands `sgpt_artifacts.zip` / `corpus.zip` dropped in directly.
2. **Colab secrets** (key icon): `GH_PAT` (repo read token, needed while
   the repo is private) and optionally `OPENAI_API_KEY`.

## Every session (GPU runtime!)

```python
# Cell 1 — fetch the repo (public, publish branch)
!git clone -q -b publish https://github.com/srinivasvillageminds/incidentwise.git /content/incidentwise
%cd /content/incidentwise

# Cell 2 — everything else
%run colab_bootstrap.py
```

~5–8 minutes later it prints a **public `*.trycloudflare.com` link** (share
for demos; alive only while the session runs) and a private Colab proxy
link (just for you).

## After testing

```python
backup_to_drive()   # push new state (history.db, drills, caches) back to Drive
stop_all()          # shut down server, ollama, tunnel
```

## Notes, bluntly

- The public tunnel exposes YOUR session to anyone holding the link —
  no auth in front of it. Fine for a demo window; close it after.
- Colab free tier disconnects idle sessions; the link dies with it. For a
  link that survives, this needs a real host (small GPU VM) — different job.
- `CHAT_BACKEND`, models, and ports are constants at the top of
  `colab_bootstrap.py`. OpenAI models appear in the UI dropdown only if
  the secret exists.
- Set the Guardrails dropdown to **L2** when sharing the public link —
  strangers with a URL are what the intent classifier is for.
- Don't commit from Colab sessions; treat the branch as read-only demo
  infrastructure and develop locally.
