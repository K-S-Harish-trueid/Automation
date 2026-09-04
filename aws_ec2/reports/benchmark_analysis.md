# EC2 Benchmark & Resource Analysis

Snapshot as of 2026-08-31, instance `i-0992747c6c8c971c1` at `13.203.75.229`.

## Instance specs

| | |
|---|---|
| Type | `t3.medium` (upgraded from `t3.small` after real OOM crashes at that size — see "History" below) |
| vCPU | 2 (Intel Xeon Platinum 8259CL @ 2.5GHz) |
| RAM | 3.7 GiB usable |
| Swap | 4 GiB swapfile (currently 0B used — no memory pressure at this instance size) |
| Root volume | 15GB gp3 EBS (14G usable) |
| OS | Ubuntu Server 26.04 LTS |

## Disk usage — 66% full (8.8G / 14G, 4.7G free)

| Item | Size |
|---|---|
| Swapfile | 4.1G |
| `backend/jobs/` (4 jobs) | 677M |
| Postgres data (`/var/lib/postgresql`) | 849M |
| `backend/data/` | 28K |

**Postgres table breakdown:**
| Table | Size |
|---|---|
| `audit_log` | 339 MB |
| `historical` | 252 MB (797,393 rows) |
| `generated_records` | 2.8 MB |
| `address_pool` | 64 KB |
| `address_denylist` | 56 KB |

**Flag**: `audit_log` (339MB) is already bigger than the actual historical reference data (252MB for 797k rows), despite being "just" a change log. Worth watching — this table has no visible retention/pruning policy, so it will keep growing indefinitely as more jobs run. Not urgent, but the first thing likely to become a real disk problem over months of use.

## Storage per job

| Job | Size | Note |
|---|---|---|
| 20260827_001 | 244M | full run |
| 20260827_002 | 84M | test run, stopped partway (manual review gate) — see test report |
| 20260827_003 | 246M | full run |
| 20260827_004 | 105M | partial |

**Average: ~169MB per job.** This scales with the raw file size and how far through the pipeline a job gets (each stage can write its own intermediate output). A 23MB raw CSV input (73k rows) produced an 84MB job folder even only partway through — expect a full end-to-end run on a similar file to land in the 150-250MB range. At current job sizes, the 4.7GB of free disk supports roughly **20-25 more jobs** before needing a resize — worth planning ahead of, not reacting to.

## Memory usage — two real operations measured

### A) Running a pipeline job (73,298 rows, 23MB CSV)
Measured on the **previous t3.small** (2GB RAM) instance:
- Wall-clock time for the automated stage chain: **~12-15 seconds**
- CPU time consumed: ~74s (cumulative over an 18-min window, most of it attributable to this job)
- Peak memory: **1.65GB** for the whole service (baseline idle ~500-600MB → job itself accounts for **~1.1GB**)

### B) Seeding historical reference data (797,393 rows, ~130MB xlsx)
Measured on the same t3.small, **before** the swap fix and resize:
- **First two attempts: OOM-killed** — the kernel killed the process outright (anon-rss ~1.45-1.48GB at time of kill, likely still climbing)
- After adding a 4GB swapfile: succeeded, but took **~6 minutes** with heavy disk thrashing (40-66% I/O wait) instead of the usual tens of seconds
- Root cause: this operation reads the entire xlsx into memory in one pass before bulk-loading it — memory demand scales with file size, no chunking

### Why B is so much worse than A despite "only" 11x more rows
A pipeline job copies the DataFrame across 6 sequential stages, so its memory cost scales with *stages × rows*. The historical seed does one parse + one bulk insert, scaling with *rows × columns* only — different shape of work, not a clean multiplier. In practice this meant 800k seed rows (~1.45GB) didn't dwarf 73k job rows (~1.1GB) the way a naive 11x multiplier would suggest.

### Current state (t3.medium, post-resize)
Not yet re-measured under load at this size. Given 4GB RAM vs. the ~1.5-1.7GB peaks seen above, both operations should now complete without touching swap at all. Re-running the historical seed test on this instance would give a clean "before/after the resize" comparison for the demo if there's time.

## History / why these numbers matter
- Started on `t3.small` (2GB RAM, no swap) — historical seed OOM-crashed the app outright, twice.
- Added a 4GB swapfile — stopped the crash, but the seed operation went from "should take seconds" to "took 6 minutes of disk thrashing."
- Resized to `t3.medium` (4GB RAM) — real fix, addresses the root cause (not enough memory) rather than papering over it with slower disk-backed overflow.

## Open items / not yet done
- Nginx upload limit raised to 300MB, proxy timeouts to 10 minutes — both were needed for the historical seed to complete over HTTP without a 413/504 error.
- No automated backups (no RDS — self-hosted Postgres). See `aws_ec2/doc/AWS_DEPLOYMENT.md`.
- SSH and Postgres both currently open to `0.0.0.0/0` — deliberate, temporary choice for convenience during setup, not yet locked down.
- No HTTPS yet (needs a domain/dynamic DNS since there's no fixed IP — see conversation notes for the DuckDNS plan).
