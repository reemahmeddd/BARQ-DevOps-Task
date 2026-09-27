# AI usage disclosure

Write None if no AI was used. Otherwise record each use:

I used Claude (Anthropic), through the Claude Code desktop app, throughout this task.
Models: Claude Sonnet 5 at the start, then Claude Opus 5.5.
This file is updated as the work continues.

## 1. Environment setup
- Tool/model: Claude Code (Claude Sonnet 5, Claude Opus 5.5)
- Purpose: Step-by-step guidance for installing Git, WSL2 and Docker Desktop 
- Files or decisions affected: No project files. Decision to keep the BARQ GitLab remote as `gitlab` and push the untouched baseline and the `starter-v2.0.0` tag first.
- What you changed or rejected: I ran the installers, the clone (with the access token) and every `git push` myself.
- How you independently verified it: `git --version`, `docker version`, `docker compose version`, `docker run hello-world`, and checking the files, commits and tag on GitHub.
- Related commit: 

## 2. Investigation and fixes
- Tool/model: Claude Code (Claude Opus 5.5)
- Purpose: Claude explained the concepts, proposed hypotheses and ran most diagnostic commands, saving their output in `evidence/`. It also drafted the commit messages.
- Files or decisions affected: `docker-compose.yml`, `nginx/nginx.conf`, `Dockerfile`, `.env.example`, `evidence/`
- What you changed or rejected: I made the fix edits myself and reviewed every `git diff` before it was tested.
- How you independently verified it: every fix was retested against the running containers before committing (see `troubleshooting.md`).
- Related commit: 292a3d8 to b8e3381


## 3. Code change in app/server.py
- Tool/model: Claude Code (Claude Opus 5.5)
- Purpose: Claude wrote the `redact_url()` helper that hides the password in the `configuration_loaded` log event, and I added it to the file.
- Files or decisions affected: `app/server.py`
- What you changed or rejected: Nothing rejected.
- How you independently verified it: Rebuilt the image, checked that the log shows `barq_app:***@` and contains the password 0 times, checked `/ready`, and ran the supplied unit tests (8 tests, OK).
- Related commit: 7545b57

## 4. Password rotation
- Tool/model: Claude Code (Claude Opus 5.5)
- Purpose: Claude generated a new random database password, wrote it directly into my local `.env` without displaying it, and changed it in PostgreSQL with `ALTER ROLE`. Claude recommended rotating instead of rewriting the git history, because the history is part of the evidence.
- Files or decisions affected: local `.env` (not committed), `evidence/27-password-rotation.txt`
- What you changed or rejected: I agreed with rotating instead of rewriting history.
- How you independently verified it: A login test from app-01 showed the old password fails and the new one works. `/ready` returned 200. No committed file contains the new password.
- Related commit: b5489bd


## 5. Documentation
- Tool/model: Claude Code (Claude Opus 5.5)
- Purpose: Claude prepared a private facts sheet from the investigation and drafted `troubleshooting.md` from it, following the BARQ template. Claude also drafted this file (`AI_USAGE.md`) and `log_analysis.md`
- Files or decisions affected: `troubleshooting.md`, `AI_USAGE.md`,`log_analysis.md`
- What you changed or rejected: I reviewed both files and edited the files where needed to add my personal sayings.
- How you independently verified it: I checked each entry against the evidence files and the commit hashes in `git log`.
- Related commit: (added when committed)

## 6. Log analysis
- Tool/model: Claude Code (Claude Opus 5.5)
- Purpose: Claude helped with the code of  `scripts/analyze_logs.py`, which reads the three logs read-only and answers the log_analysis.md questions, and drafted `log_analysis.md` from the script output.
- Files or decisions affected: `scripts/analyze_logs.py`, `evidence/32-log-analysis-output.txt`, `log_analysis.md`. Decision to count one client request per unique request_id in access.log, so retries and duplicates are not counted twice.
- What you changed or rejected: I reviewed the draft and edited the file,    Three details in the first draft were wrong (the paths and times of the Redis and Postgres windows, and the Postgres latency); they were corrected after checking them against the logs.
- How you independently verified it: The numbers are consistent across the three logs (for example 59 "connection refused" lines = 40 failed + 19 retried requests), the original logs have the same sha256 checksums before and after the analysis, and the examples in log_analysis.md can be found by request_id in the log files.
- Related commit: 5872b98 (script and output), log_analysis.md commit (added when committed)

You may use AI and external resources. You must understand and demonstrate the work.
