#!/bin/bash
set -e

# 4GB swap -- without this, a large historical-data seed upload
# (Historical_Dataset.xlsx, ~800k rows) reliably OOM-kills the app on a
# small instance (t3.small's 2GB RAM isn't enough for the in-memory
# xlsx-parse + bulk-insert). Swap doesn't make it fast, but it's the
# difference between "slow" and "the whole service getting killed and
# restarted mid-request". See AWS_DEPLOYMENT.md for the full story.
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

apt update -y
apt install -y python3-pip python3-venv git nginx postgresql postgresql-contrib

# Debian/Ubuntu's postgresql package auto-initializes and starts the cluster
# on install (unlike RHEL, no separate "initdb" step needed).
systemctl enable --now postgresql

# Set a password for the postgres role and create the app database.
# NOTE: '1234' is a weak placeholder for initial setup only -- change it
# later (ALTER USER postgres PASSWORD '...') and update .env to match.
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '1234';"
sudo -u postgres createdb k2_historical

# Ubuntu's default site is marked default_server and wins over conf.d/*.conf
# unless removed -- without this, port 80 serves the "Welcome to nginx" page
# instead of proxying to the app.
rm -f /etc/nginx/sites-enabled/default

# Nginx reverse proxy -> app on 127.0.0.1:8000
cat > /etc/nginx/conf.d/k2.conf <<'EOF'
server {
    listen 80;
    client_max_body_size 300M;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_connect_timeout 600s;
    }
}
EOF
systemctl enable --now nginx

# systemd unit for the app (not started yet -- .env with real DB creds isn't there yet).
# Deliberately NO EnvironmentFile= line here -- the app already loads .env
# itself via python-dotenv (rules_config.py's load_dotenv()), identically to
# local dev. Adding EnvironmentFile= on top of that double-loads it through
# systemd's OWN separate parser, which corrupts any value containing a
# backslash escape (e.g. NATIONAL_ID_REGEX=\d{12} silently became d{12} --
# systemd strips the backslash -- which flagged ~57,000 genuinely valid
# National ID accounts as invalid before this was found and fixed). One
# loading mechanism, not two, for the same file.
cat > /etc/systemd/system/k2.service <<'EOF'
[Unit]
Description=K2 Automation
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/k2-automation
ExecStart=/home/ubuntu/k2-automation/venv/bin/python run.py --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable k2
