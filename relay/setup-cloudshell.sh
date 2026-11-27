#!/usr/bin/env bash
# Déploie le relais Webtop dans Google Cloud (Tel Aviv). À lancer dans Cloud Shell :
#   bash <(curl -fsSL https://raw.githubusercontent.com/nicolasrames-bit/devoirs-nathan/main/relay/setup-cloudshell.sh)
set -euo pipefail

PROJECT=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT" ]; then
  echo "❌ Aucun projet sélectionné. Choisis ton projet en haut de la console Google Cloud, puis relance."
  exit 1
fi
echo "▶ Projet : $PROJECT"

echo "▶ Activation des services (1 à 2 min)…"
gcloud services enable cloudfunctions.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com --quiet

DIR="$HOME/webtop-relay"
mkdir -p "$DIR" && cd "$DIR"
RAW="https://raw.githubusercontent.com/nicolasrames-bit/devoirs-nathan/main/relay"
curl -fsSL "$RAW/main.py" -o main.py
curl -fsSL "$RAW/requirements.txt" -o requirements.txt

# Réutilise le jeton existant si le relais a déjà été déployé
TOKEN_FILE="$DIR/.token"
[ -f "$TOKEN_FILE" ] || openssl rand -hex 24 > "$TOKEN_FILE"
TOKEN=$(cat "$TOKEN_FILE")

echo "▶ Déploiement du relais à Tel Aviv (2 à 4 min)…"
gcloud functions deploy webtop-relay --gen2 --runtime=python312 --region=me-west1 \
  --source=. --entry-point=relay --trigger-http --allow-unauthenticated \
  --memory=256Mi --max-instances=2 --set-env-vars="RELAY_TOKEN=$TOKEN" --quiet

URL=$(gcloud functions describe webtop-relay --gen2 --region=me-west1 --format='value(serviceConfig.uri)')

echo
echo "✅ Relais prêt. Ajoute ces 2 secrets dans GitHub (Settings → Secrets and variables → Actions) :"
echo
echo "   WEBTOP_RELAY_URL   = $URL"
echo "   WEBTOP_RELAY_TOKEN = $TOKEN"
echo
