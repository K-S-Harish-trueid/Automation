# How to Run K2 Automation on AWS

A step-by-step guide for setting this up on a fresh AWS EC2 instance, written
for someone who hasn't done this before. Follow it top to bottom the first
time; skip to the relevant section if you're troubleshooting something
specific later.

**Architecture in one sentence**: one EC2 instance runs the app *and*
Postgres together (no separate database service like RDS) — simple and
cheap, at the cost of you being responsible for your own backups. See
"What this setup does NOT give you" at the bottom before you rely on this
for anything important.

---

## Part 1 — One-time AWS account setup

Skip this whole part if the AWS account already has an admin IAM user set up.

1. Sign in to the AWS Console as the **root user** (only ever for account
   setup — never for day-to-day work).
2. Enable MFA on the root account: IAM → Security credentials → Assign MFA
   device.
3. Set a billing alert so you don't get surprised: Billing → Budgets →
   create a budget (e.g. $20/month).
4. Create an admin IAM user instead of using root day-to-day:
   IAM → User groups → create group `admins` → attach policy
   `AdministratorAccess` → IAM → Users → create a user, add to `admins`,
   enable console access and MFA.
5. From now on, sign in as that IAM user, not root.

---

## Part 2 — Create the IAM role the EC2 instance will use

This lets the instance have AWS permissions (e.g. for CloudWatch later)
without embedding credentials on the box.

1. IAM → Roles → **Create role**.
2. Trusted entity type: **AWS service**. Use case: **EC2**. Next.
3. Attach permissions: none needed for the base setup — click Next without
   checking anything (add specific policies later only if you need them,
   e.g. S3 access for backups).
4. Name it `k2-app-role`, create it. This automatically creates a matching
   instance profile with the same name (EC2-use-case roles do this
   automatically — no separate step needed).

---

## Part 3 — Launch the EC2 instance

1. EC2 → **Launch instance**.
2. **Name**: `k2-automation`
3. **AMI**: Ubuntu Server 24.04 (or newer) LTS, 64-bit x86.
4. **Instance type**: `t3.small` minimum. (t3.micro is too tight on RAM —
   see the swap note in the user-data script below for why.)
5. **Key pair**: create a new one if you don't have one. **Download the
   `.pem` file and keep it somewhere safe** — this is the only way to SSH
   in, and AWS won't let you download it again.
6. **Network settings** → Edit:
   - Auto-assign public IP: **Enable**
   - Firewall: **Create security group**, allow:
     - SSH (22) from **your IP** (not Anywhere, unless you have a specific
       reason — see "Security notes" below)
     - HTTP (80) from Anywhere
     - HTTPS (443) from Anywhere
7. **Configure storage**: bump the root volume to **at least 20 GiB** (15GB
   works but leaves little headroom once Postgres data + the swapfile +
   generated job files share the same disk).
8. **Advanced details**:
   - IAM instance profile: select `k2-app-role`
   - **User data**: paste the entire contents of this repo's
     [`ec2-user-data.sh`](ec2-user-data.sh) into the box. This one script
     does *all* of the following automatically on first boot:
     - Adds 4GB of swap (prevents an out-of-memory crash during the
       historical-data seed later — see Part 6)
     - Installs Python, Nginx, and Postgres
     - Creates the `k2_historical` Postgres database
     - Sets the `postgres` role's password to `1234` (a placeholder —
       change it once you're actually using this for real, see Part 4)
     - Configures Nginx as a reverse proxy (port 80 → the app's port 8000),
       with a large upload limit and long timeouts (needed for the
       historical data upload)
     - Creates (but does not yet start) the systemd service that will run
       the app
9. Review the summary, click **Launch instance**.
10. Once it shows "Running", note its **Public IPv4 address** — you'll need
    it for everything below.

### Getting your instance's IP into commands below
Every command below that shows `<PUBLIC_IP>` means: replace it with your
instance's actual public IPv4 address from the EC2 console.

---

## Part 4 — Connect and deploy the app

### 4a. SSH in

**Windows (PowerShell)**:
```powershell
icacls .\your-key.pem /inheritance:r
icacls .\your-key.pem /remove:g "BUILTIN\Users"
icacls .\your-key.pem /grant:r "$($env:USERNAME):(R)"
ssh -i .\your-key.pem ubuntu@<PUBLIC_IP>
```
The `icacls` lines fix a Windows-only issue: SSH refuses to use a key file
that's readable by more than just you. If you still get a permissions
error after this, check `icacls .\your-key.pem` and strip whatever group
still shows up the same way.

**Mac/Linux**:
```bash
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@<PUBLIC_IP>
```

First connection will ask "Are you sure you want to continue connecting?"
— type `yes`.

### 4b. Verify the user-data script actually finished

```bash
cloud-init status --wait      # should print "status: done"
sudo systemctl status nginx postgresql   # both should say "active (running)"
```

### 4c. Clone the repo and install

```bash
git clone https://github.com/K-S-Harish-trueid/Automation.git ~/k2-automation
cd ~/k2-automation
./install.sh
```

First run of `install.sh` creates the Python virtual environment, then
stops after copying `.env.example` to `.env` — you need to edit that file
before continuing.

### 4d. Edit `.env`

```bash
nano .env
```

The one line that **must** change from the template:
```
DATABASE_URL=postgresql+psycopg://postgres:1234@localhost:5432/k2_historical
```
(`.env.example`'s default port `5433` is a local-dev artifact — this server
uses Postgres's normal port, `5432`. The password `1234` matches what the
user-data script set — change both here and on the Postgres side together
if you want a real password, see "Security notes" below.)

Save (Ctrl+O, Enter) and exit (Ctrl+X).

### 4e. Run install.sh again to start the app

```bash
./install.sh
```

This time it installs dependencies and starts the `k2` systemd service.
Check it's actually up:
```bash
sudo systemctl status k2
```
Should say `active (running)`.

### 4f. Confirm it's reachable

Open `http://<PUBLIC_IP>` in a browser. You should see the K2 Automation
frontend.

---

## Part 5 — Change the Postgres password (do this before real use)

`1234` is fine for getting things running, not fine to leave in place.

```bash
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '<new-password>';"
```
Then update `~/k2-automation/.env`'s `DATABASE_URL` to match, and:
```bash
sudo systemctl restart k2
```

---

## Part 6 — Seed the reference data

The app needs two datasets that are **never stored in git** (too large /
environment-specific) — you seed them yourself through the browser.

1. Open `http://<PUBLIC_IP>/seed` — an unlisted admin page (not linked from
   the main app) with two panels.
2. **Historical reference data** panel: upload `Historical_Dataset.xlsx`
   (the big one, ~120MB+, ~800k rows). This can take several minutes —
   that's expected, don't refresh the page or resubmit. The swap space set
   up in Part 3 is what keeps this from crashing the app on a small
   instance; it's still slow, just not fatal.
3. **Address pools** panel: upload `Address_Pools.xlsx` (small, this
   repo's `backend/data/Address_Pools.xlsx` if you have a copy — otherwise
   ask whoever last generated one, or see `backend/data/migrate_address_pools.py`
   for the hardcoded fallback data). Without this, the address-fix pipeline
   stage can't auto-repair invalid addresses for provinces it doesn't
   recognize.

Check both worked:
```bash
curl http://localhost/api/historical/status
curl http://localhost/api/seed/status
```
Both should report `"seeded": true` with non-zero row/entry counts.

---

## Part 7 — Run jobs

From `http://<PUBLIC_IP>`, use the dashboard normally — upload a raw file,
walk it through the pipeline stages, download the final output. This part
works the same on AWS as it does running locally; nothing AWS-specific
about it.

One thing worth knowing: the pipeline moves a row through stages
automatically except for one edge case in the address-fix stage — a row
whose province isn't recognized in the address pool data stays flagged
invalid with no further review step catching it (see
`rules/06-address_fix.txt` if you need the details). Seeding the address
pools properly (Part 6) is what prevents this from happening in practice.

---

## Day-to-day operations

### Stopping the instance when not in use (saves money)
EC2 console → select instance → **Instance state → Stop instance**. This is
safe — the disk (and everything on it: the database, the code, everything)
is untouched, you're just not paying for compute time while it's off.

**Never click "Terminate"** — that deletes the disk too (see "What this
setup does NOT give you" below for why that matters more than it should
right now).

### Starting it back up
EC2 console → **Instance state → Start instance**. Takes a minute or two.
**The public IP will be different** every time you do this (unless you've
set up an Elastic IP) — check the EC2 console for the new one.

### Redeploying after a code change
```bash
ssh -i your-key.pem ubuntu@<PUBLIC_IP>
cd ~/k2-automation
git pull
./install.sh
```
`install.sh` is safe to re-run any time — it reinstalls dependencies (fast
if nothing changed) and restarts the service.

### Checking logs
```bash
sudo journalctl -u k2 -f          # live app logs, Ctrl+C to stop following
sudo journalctl -u k2 --no-pager -n 50   # last 50 lines
sudo tail -f /var/log/nginx/access.log   # who's hitting the server
sudo tail -f /var/log/nginx/error.log    # nginx-level errors (502s, etc.)
```

### Checking disk space
```bash
df -h /
free -h    # RAM + swap usage
```
Watch this over time — Postgres data, the swapfile, and generated job
output all share one disk. If it climbs past ~80%, resize the EBS volume
(can be done live, without downtime, from the EC2 console's volume page).

---

## Security notes (read before treating this as production-serious)

This setup, as documented above, has several things left deliberately
loose for simplicity while getting it running. None of them are secret —
they were flagged along the way — but collecting them here so nobody
mistakes "runs fine" for "locked down":

- **SSH is open to the whole internet** (`0.0.0.0/0`), not restricted to a
  specific IP. Fine for a low-stakes/testing box; tighten it (Security
  Groups → edit the port 22 rule → source = your IP `/32`) before this
  matters.
- **No backups exist.** There's no RDS, so there's no automatic snapshot
  or point-in-time recovery. If you need this to be safe against data
  loss, the simplest fix is a cron job running `pg_dump` on a schedule,
  optionally copied to S3. Not set up as of this writing.
- **`Delete on Termination` may still be enabled on the root volume** —
  check EC2 → Instance → Storage → the volume → confirm this is set to
  **No**, so that even a full instance Terminate doesn't wipe the
  database with it. (Stop is always safe regardless; this only matters
  for Terminate.)
- **No HTTPS.** The app is served over plain `http://`. Setting this up
  needs: a stable IP (Elastic IP), a domain or free subdomain (e.g.
  DuckDNS) pointed at it, then `sudo apt install certbot
  python3-certbot-nginx && sudo certbot --nginx -d your-domain.com` —
  free, and auto-renewing. Not set up as of this writing.

None of these block getting the app running and useful — they're the
"before you'd trust this with something you can't afford to lose" list.

---

## What this setup does NOT give you

Compared to a more heavyweight setup (e.g. a managed RDS database):

- No automatic backups or point-in-time recovery
- No automatic failover if the instance dies
- No separate scaling of the app vs. the database
- Losing the EBS volume (accidental Terminate with delete-on-termination
  still on, or an EBS failure) means losing everything, with no recovery
  path

This is a deliberate tradeoff for a small internal tool — cheap and simple
now, in exchange for you being the backup plan. Worth revisiting if this
tool's importance grows.
