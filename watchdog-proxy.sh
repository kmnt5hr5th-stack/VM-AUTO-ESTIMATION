#!/bin/bash
# Vérifie si le proxy LBC tourne, le relance si nécessaire

LOG="/tmp/vm-lbc-watchdog.log"

# Vérifier si le proxy local répond
if curl -s --max-time 3 http://localhost:8080/health | grep -q "ok"; then
    exit 0  # Proxy OK, rien à faire
fi

echo "[$(date)] Proxy mort — relance..." >> "$LOG"
bash /Users/makenvalerie/vm-auto-estimation/start-proxy.sh
echo "[$(date)] Relance terminée" >> "$LOG"
