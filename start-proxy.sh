#!/bin/bash
# Démarre le proxy LBC + tunnel Cloudflare + met à jour Railway automatiquement

RAILWAY_TOKEN="b883227c-f869-4ce0-9e9d-6c5cc0f4711f"
PROJECT_ID="7ff24af2-085d-4725-a60c-417742a652d7"
SERVICE_ID="d64aba0b-0b07-4875-a287-80757e03febb"
ENV_ID="64e63a4c-8864-4ba9-a11c-fbd5ad20f60a"
LOG="/tmp/vm-lbc-proxy.log"
CF_LOG="/tmp/cloudflared-vm.log"

echo "[$(date)] Démarrage proxy LBC..." >> "$LOG"

# Attendre la connexion réseau
sleep 15

# Tuer les anciens processus
pkill -f "uvicorn lbc-proxy" 2>/dev/null
pkill -f "cloudflared tunnel" 2>/dev/null
sleep 2

# Démarrer uvicorn
cd /Users/makenvalerie/vm-auto-estimation
/Users/makenvalerie/vm-auto-estimation/venv/bin/uvicorn lbc-proxy.main:app \
    --host 0.0.0.0 --port 8080 >> "$LOG" 2>&1 &

sleep 5

# Démarrer cloudflared et capturer l'URL
> "$CF_LOG"
/opt/homebrew/bin/cloudflared tunnel --url http://localhost:8080 --no-autoupdate >> "$CF_LOG" 2>&1 &

# Attendre l'URL (max 60 secondes)
TUNNEL_URL=""
for i in $(seq 1 30); do
    sleep 2
    TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$CF_LOG" | head -1)
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
done

if [ -z "$TUNNEL_URL" ]; then
    echo "[$(date)] ERREUR: Impossible d'obtenir l'URL du tunnel" >> "$LOG"
    exit 1
fi

echo "[$(date)] Tunnel URL: $TUNNEL_URL" >> "$LOG"

# Mettre à jour Railway
curl -s -X POST https://backboard.railway.app/graphql/v2 \
    -H "Authorization: Bearer $RAILWAY_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"mutation { variableCollectionUpsert(input: { projectId: \\\"$PROJECT_ID\\\", environmentId: \\\"$ENV_ID\\\", serviceId: \\\"$SERVICE_ID\\\", variables: { LBC_PROXY_URL: \\\"$TUNNEL_URL\\\" } }) }\"}" \
    >> "$LOG" 2>&1

echo "[$(date)] Variable Railway mise à jour" >> "$LOG"

# Redéployer Railway pour prendre en compte la nouvelle URL
sleep 3
curl -s -X POST https://backboard.railway.app/graphql/v2 \
    -H "Authorization: Bearer $RAILWAY_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"mutation { serviceInstanceRedeploy(serviceId: \\\"$SERVICE_ID\\\", environmentId: \\\"$ENV_ID\\\") }\"}" \
    >> "$LOG" 2>&1

echo "[$(date)] Redéploiement Railway lancé — proxy prêt." >> "$LOG"
