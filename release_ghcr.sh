#!/usr/bin/env bash
set -euo pipefail

# ========= CONFIG =========
GHCR_USER=""       
GHCR_OWNER=""       
IMAGE_NAME=""
GHCR_TOKEN=""
# ==========================

# Couleurs ANSI
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'; C_RED=$'\033[1;31m'; C_YELLOW=$'\033[1;33m'
else
  C_RESET=''; C_BLUE=''; C_GREEN=''; C_RED=''; C_YELLOW=''
fi
step() { echo "${C_BLUE}==>${C_RESET} $*"; }
ok()   { echo "${C_GREEN}[OK]${C_RESET} $*"; }
warn() { echo "${C_YELLOW}[ATTENTION]${C_RESET} $*"; }
err()  { echo "${C_RED}[ERREUR]${C_RESET} $*" >&2; }

usage() {
  echo "Usage: $0 <version> [--latest]"
  echo "Ex: $0 1.2.3 --latest"
  exit 1
}

[[ $# -lt 1 ]] && usage
VERSION="$1"
PUSH_LATEST=false
[[ "${2:-}" == "--latest" ]] && PUSH_LATEST=true

# ── 0. Vérif ────────────────────────────────────────────────────────────────
command -v docker >/dev/null || { err "Docker requis."; exit 1; }
: "${GHCR_USER:?GHCR_USER manquant}"
: "${GHCR_OWNER:?GHCR_OWNER manquant}"
: "${IMAGE_NAME:?IMAGE_NAME manquant}"
: "${VERSION:?VERSION manquante}"
: "${GHCR_TOKEN:?GHCR_TOKEN manquant (export GHCR_TOKEN=...)}"

# ── 1. Audit dépendances Python ────────────────────────────────────────────
step "pip-audit…"
if command -v pip-audit >/dev/null; then
  pip-audit || { err "CVE détectée — release annulée."; exit 1; }
else
  warn "pip-audit absent, scan ignoré (pip install pip-audit)."
fi

IMG="ghcr.io/${GHCR_OWNER}/${IMAGE_NAME}:${VERSION}"

step "Login GHCR (login: ${GHCR_USER} → owner: ${GHCR_OWNER})…"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

step "Build ${IMG}…"
docker build -t "$IMG" .

# ── 2. Scan image Docker ───────────────────────────────────────────────────
step "Trivy scan ${IMG}…"
if command -v trivy >/dev/null; then
  trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed "$IMG" \
    || { err "Vulnérabilité HIGH/CRITICAL corrigeable dans l'image — release annulée."; exit 1; }
else
  warn "Trivy absent, scan ignoré (brew install trivy)."
fi

if $PUSH_LATEST; then
  step "Tag latest…"
  docker tag "$IMG" "ghcr.io/${GHCR_OWNER}/${IMAGE_NAME}:latest"
fi

step "Push ${IMG}…"
docker push "$IMG"

if $PUSH_LATEST; then
  step "Push latest…"
  docker push "ghcr.io/${GHCR_OWNER}/${IMAGE_NAME}:latest"
fi

ok "Fini. Image: $IMG"
$PUSH_LATEST && ok "Tag latest poussé."
