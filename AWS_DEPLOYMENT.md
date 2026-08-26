# AWS Deployment Notes — K2 Automation

Running log of the AWS setup for this project, so it can be picked back up
without re-deriving everything. Architecture: **one EC2 instance running the
app + Postgres together** (no RDS — decided against it to keep cost/complexity
down; see "Decisions" below).

## Resources created so far

| Resource | ID / value |
|---|---|
| AWS Account ID | `823787500234` |
| Region | `ap-south-1` (Mumbai) |
| VPC (in use) | `vpc-0bcb2403bc829bdc5` — the account's **default VPC** |
| VPC (abandoned) | `vpc-06065aba81bcb3a97` (`k2_automation`) — custom VPC we started building, then dropped in favor of the default VPC (no subnets were ever created in it, safe to delete later) |
| Security group | `launch-wizard-1` |
| IAM role / instance profile | `k2-app-role` — ARN: `arn:aws:iam::823787500234:instance-profile/k2-app-role` — currently has **no attached policies** (add scoped ones later only if needed, e.g. S3 for backups) |
| Key pair | `keygen1` (private key file: `keygen1.pem`, kept locally in this repo's root — **not committed to git**, check `.gitignore`) |
| EC2 instance | `i-0992747c6c8c971c1`, name `k2-automation` |
| AMI | `ami-01a00762f46d584a1` — Ubuntu Server 26.04 LTS, username `ubuntu` |
| Instance type | `t3.small` (2 vCPU, 2GiB RAM, unlimited CPU credits) |
| Root volume | 15 GiB gp3 |
| Public IP | `65.2.57.174` — **auto-assigned, not an Elastic IP** — this will change if the instance is stopped/started. Allocate an Elastic IP before relying on this address anywhere (DNS, bookmarks, etc.) |

## Security group rules (`launch-wizard-1`)

| Port | Source | Note |
|---|---|---|
| 22 (SSH) | `0.0.0.0/0` | **Temporary — lock to your IP before this is anything beyond testing.** `curl checkip.amazonaws.com` to get your IP, then revoke the open rule and re-add scoped to `<your-ip>/32`. |
| 80 (HTTP) | `0.0.0.0/0` | Fine as-is — needed for the reverse proxy / future TLS challenge |
| 443 (HTTPS) | `0.0.0.0/0` | Fine as-is |

## What the EC2 user-data script did on first boot

File: [`ec2-user-data.sh`](ec2-user-data.sh) in this repo — pasted into the
launch wizard's "User data" field at launch time.

- Installed `python3-pip`, `python3-venv`, `git`, `nginx`, `postgresql`, `postgresql-contrib`
- Started and enabled `postgresql` (Ubuntu auto-initializes the cluster on install)
- Set the `postgres` role's password to **`1234`** (placeholder — change this, see TODO)
- Created database `k2_historical`
- Wrote an Nginx reverse-proxy config (`/etc/nginx/conf.d/k2.conf`) forwarding `:80` → `127.0.0.1:8000`
- Wrote a systemd unit (`/etc/systemd/system/k2.service`) for the app, `enable`d but **not started** (no `.env` yet)

Verify it all ran cleanly with:
```bash
sudo cat /var/log/cloud-init-output.log
cloud-init status --wait
sudo systemctl status nginx postgresql
sudo -u postgres psql -c "\l"    # should list k2_historical
```

## Connecting

```powershell
ssh -i .\keygen1.pem ubuntu@65.2.57.174
```

### If SSH says "Bad permissions" / "UNPROTECTED PRIVATE KEY FILE"

Windows OpenSSH refuses a key file readable by more than just you. Fix:
```powershell
icacls .\keygen1.pem /inheritance:r                              # strip inherited permissions
icacls .\keygen1.pem /remove:g "NT AUTHORITY\Authenticated Users" # if present
icacls .\keygen1.pem /remove:g "BUILTIN\Users"                    # if present — this is usually the real culprit
icacls .\keygen1.pem /grant:r "$($env:USERNAME):(R)"              # grant YOUR account explicitly
```
Note the last step: being a member of `Administrators` is **not enough** —
a non-elevated terminal doesn't run with that group's rights active (UAC
filtering), so the ACL must name your actual account, not just rely on group
membership. Check the ACL anytime with `icacls .\keygen1.pem`.

## DATABASE_URL for this box

Postgres lives locally now (not RDS), default port, so `.env` should have:
```
DATABASE_URL=postgresql+psycopg://postgres:1234@localhost:5432/k2_historical
```
(Note: different from `.env.example`'s `localhost:5433` — that port was
specific to a local dev setup, this server uses Postgres's normal default
port 5432.)

## Remaining steps (in order)

1. SSH in, verify user-data succeeded (commands above).
2. Clone the repo, create venv, install requirements:
   ```bash
   git clone <repo-url> ~/k2-automation
   cd ~/k2-automation
   python3 -m venv venv
   source venv/bin/activate
   pip install -r backend/requirements.txt
   ```
3. Write `.env` in the repo root on the server (copy from `.env.example`, fix `DATABASE_URL` as above).
4. `sudo systemctl start k2` and check `sudo systemctl status k2`.
5. Confirm the app responds: `curl http://localhost:8000` on the box, then `http://65.2.57.174` from your own machine.
6. **Change the Postgres password** from the `1234` placeholder:
   ```bash
   sudo -u postgres psql -c "ALTER USER postgres PASSWORD '<new-password>';"
   ```
   and update `.env` + restart `k2` to match.
7. Lock SSH down to your IP only (see security group table above).
8. Allocate an Elastic IP and associate it with the instance, so the public IP stops changing.
9. Point a domain at the Elastic IP, then run certbot for HTTPS:
   ```bash
   sudo apt install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d your-domain.com
   ```
10. Set up backups — no RDS means no automatic ones. Simplest: a nightly cron running `pg_dump` (optionally synced to S3). Not yet set up — revisit.
11. Watch disk usage (`df -h`) over time — Postgres data, WAL, and the app's `jobs/` output all now share the same 15GB volume that used to be sized for the app alone. Resize the EBS volume (non-destructive, done live) if it fills up.

## Decisions made along the way (context for "why", not just "what")

- **Default VPC over a custom one** — a custom VPC (with its own subnets/IGW/route tables) was started but abandoned; for a single-instance app it added failure points (e.g. a subnet mislabeled "private" that was actually routed like a public one) for no real benefit. The default VPC already has working public subnets and an IGW.
- **Postgres on the same EC2 box instead of RDS** — trades away RDS's automatic backups, patching, and Multi-AZ failover in exchange for zero extra monthly cost and one less moving part. Deliberate choice for this internal tool's scale; backups need to be handled manually as a result (see step 10 above).
- **Ubuntu over Amazon Linux 2023** — purely a preference call; the user-data script and systemd unit are written for Ubuntu's `apt` and `ubuntu` user specifically.
