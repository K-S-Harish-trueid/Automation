#!/bin/bash
set -e
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
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
systemctl enable --now nginx

# systemd unit for the app (not started yet -- .env with real DB creds isn't there yet)
cat > /etc/systemd/system/k2.service <<'EOF'
[Unit]
Description=K2 Automation
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/k2-automation
EnvironmentFile=/home/ubuntu/k2-automation/.env
ExecStart=/home/ubuntu/k2-automation/venv/bin/python run.py --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable k2
