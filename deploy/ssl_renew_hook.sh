#!/bin/bash
# Let's Encrypt certificate renewal deploy hook for xmuoj.com
# Called by certbot after each successful renewal.
# Copies renewed certs into Docker volume and reloads nginx.
set -e
cp /etc/letsencrypt/live/xmuoj.com/fullchain.pem /OnlineJudgeDeploy/data/backend/ssl/server.crt
cp /etc/letsencrypt/live/xmuoj.com/privkey.pem /OnlineJudgeDeploy/data/backend/ssl/server.key
chmod 600 /OnlineJudgeDeploy/data/backend/ssl/server.key
docker exec oj-backend supervisorctl -c /app/deploy/supervisord.conf restart nginx
