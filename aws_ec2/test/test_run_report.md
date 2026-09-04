# Test Run Report — Pipeline Job on EC2

## What was tested
A real pipeline job, submitted directly to the deployed EC2 instance's API (not through the browser), to measure actual server-side timing and resource use rather than guessing from local dev behavior.

**Working data used**: `dummy_data/Stage 1/K2_Raw_file.csv` (23MB, 73,298 rows) — already present in this repo, not duplicated here to avoid a second copy of a 23MB file in git. Same fixture referenced by `backend/data/seed_historical.py`'s default source and the local `testing/` scripts.

## How it was run
```bash
curl -s -w "\nHTTP:%{http_code} TIME:%{time_total}s" \
  -X POST http://<instance-ip>/api/jobs \
  -F "file=@dummy_data/Stage 1/K2_Raw_file.csv"
```
Then polled `GET /api/jobs/<job_id>/progress` every 3 seconds until the job settled.

## Result
```json
{"job_id":"20260827_002","status":"processing"}
```
HTTP 200, upload+response time 3.4s (mostly the 23MB network transfer).

**Progress over time:**
| t+ | status | stage |
|---|---|---|
| 3s | processing (33%) | Inputs required from CMS |
| 6s | processing (33%) | Inputs required from CMS |
| 9s | processing (50%) | Missing ID & DoB |
| 12s | processing (83%) | Missing Mobile Numbers |
| 15s | **idle (83%)** | Missing Mobile Numbers |

Job auto-advanced through 4 stages in **~12-15 seconds**, then correctly stopped at "Missing Mobile Numbers" — a manual review gate, expected pipeline behavior, not a failure.

## Resource cost (measured on the t3.small instance, pre-resize)
Via `systemctl show k2 -p CPUUsageNSec,MemoryPeak` since the service's last restart (~18 min prior, during which this job was effectively the only real work done):

| Metric | Value |
|---|---|
| CPU time consumed | 74 seconds |
| Peak memory (whole service) | 1.65GB (idle baseline ~500-600MB → job itself ~1.1GB) |

See `aws_ec2/reports/benchmark_analysis.md` for the full comparison against the historical-data seed operation and current (t3.medium) specs.

## Resulting job's disk footprint
`backend/jobs/20260827_002/` — **84MB** (job only progressed partway through the pipeline; a full end-to-end run on a similarly-sized file would be expected to land higher, in the 150-250MB range based on the other 3 jobs measured — see benchmark report).

## Follow-on findings from this same server (not job-specific, surfaced during the same session)
- **`address_fix` invalid-address rules** — reviewed real flagged addresses and found two categories the existing rules missed (a real place name with junk numbers glued on, and a place name with a phone number pasted in). Two new rules added and verified against both the original examples and a simulated real Iraqi postal-code address (to confirm no false positive) — see `rules/06-address_fix.txt` and `backend/app/pipeline/toolbox.py`.
- **Nginx defaults were too small** for the historical-data upload (413 on body size, then 502/504 on timeout) — fixed (`client_max_body_size 300M`, 10-minute proxy timeouts), now baked into `aws_ec2/doc/ec2-user-data.sh` for future launches.
