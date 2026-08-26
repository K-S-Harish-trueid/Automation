#!/bin/bash
# Run this from inside the cloned repo on the EC2 box after `git clone` /
# `git pull`. Safe to re-run — sets up the venv if missing, always refreshes
# dependencies, and restarts the app service.
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creating venv..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r backend/requirements.txt
deactivate

if [ ! -f .env ]; then
    echo "No .env found -- copying .env.example. EDIT IT before starting the app:"
    echo "  - DATABASE_URL must point at localhost:5432 (this box's local Postgres), not the .env.example default"
    cp .env.example .env
    echo "Stopping here so you can edit .env. Re-run install.sh once it's set."
    exit 0
fi

echo "Restarting k2 service..."
sudo systemctl restart k2
sleep 1
sudo systemctl status k2 --no-pager -l
