# Evidence and submission index

- Repository URL: https://github.com/reemahmeddd/BARQ-DevOps-Task
- Final commit: the latest commit on `main` (see the submission message)
- Matching CI run: https://github.com/reemahmeddd/BARQ-DevOps-Task/actions (run for the final commit)
- Continuous video URL: https://youtu.be/PkC1_7ZKudc
- Challenge receipt ID: 83bb183c37e3475882619fdf658ad540 (started 2026-09-28T00:33:25Z)
- Starting video commit: 4b713f2
- Video commit (on camera): 23a669a "feat: add app-03 and move the public port to 8080"
- Later commits: d34a4f9 (evidence only: nginx recreated so 8090 answers) and documentation-only commits
  that update README, security_review.md and this index to the final state.

## Honest notes about the video
- The video is one continuous, unedited recording of about 28 minutes, longer than the requested
  12-18 minutes.
- I did not note timestamps while recording, so the video column below is empty.
- Port change: `.env` and `.env.example` were changed to 8090 on camera, but nginx was not recreated,
  so 8090 did not answer in the video. After the recording I ran `docker compose up -d --wait nginx`;
  8090 then answered and `validate.py` passed 46/46 (`evidence/46-post-video-port-8090.txt`). No
  configuration was changed after the video.
- The message of commit 23a669a says "8080" by mistake; the committed change is 8090.

## Requirement -> file/output -> commit -> video
| Requirement | File / output | Commit | Video |
|---|---|---|---|
| Baseline kept, starter commit | `git log`, tag `starter-v2.0.0` | 8442da3 | shown |
| Investigation journal | `troubleshooting.md`, `evidence/01`-`45` | 292a3d8 ... 8ccc646 | - |
| Log analysis | `log_analysis.md`, `scripts/analyze_logs.py`, `evidence/32` | 5872b98, 576a20d | shown |
| Two instances behind nginx, isolated networks, only nginx published | `docker-compose.yml`, `nginx/nginx.conf`, `evidence/20`, `evidence/33` | ff7de2b, f2daf3b | shown |
| Health/readiness, startup order, restart, limits | `docker-compose.yml`, `evidence/37`, `39`, `31` | aede8a3, 347068f, b8e3381 | shown |
| Secrets out of repo/image/logs | `.env.example`, `evidence/23`-`27` | 7236e9a, b5489bd | - |
| Endpoints `/ /health /ready /records /counter /instance` | `validate.py` output | 6b87238 | shown |
| Validation script | `validate.py`, `evidence/33` | 6b87238 | shown |
| Failure test | `failure_test.py`, `evidence/34` | deee0d6 | shown |
| Backup / restore | `backup.sh`, `restore.sh`, `evidence/35` | 89c2671 | - |
| Record survives recreation | `evidence/18` | cedded0 | shown |
| CI | `.github/workflows/ci.yml` | 02841e9, a312d6b | - |
| Challenge run once and repaired | `.assessment/challenge.json` (receipt above) | - | shown |
| Port 8090 | `.env.example`, `evidence/46` | 23a669a, d34a4f9 | shown (did not answer, see notes) |
| Third instance | `docker-compose.yml`, `nginx/nginx.conf` | 23a669a | shown |
| Decisions, security review, diagram, AI disclosure | `decisions.md`, `security_review.md`, `architecture.png`, `AI_USAGE.md` | c2ac529, 255f98f, eba55c4, 4b713f2 | - |
