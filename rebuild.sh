#!/usr/bin/env bash
# rebuild.sh — Rebuild the Django app image, replace the running container,
#              and delete the old image so it doesn't pile up.
#
# Usage:  ./rebuild.sh
set -euo pipefail

log()     { echo -e "\033[1;34m▶\033[0m $*"; }
success() { echo -e "\033[1;32m✓\033[0m $*"; }
warn()    { echo -e "\033[1;33m⚠\033[0m $*"; }

CONTAINER="django_app"
SERVICE="web"
NGINX_CONTAINER="nexgen_nginx"

# ── 1. Capture the current image ID before building ──────────────────────────
OLD_IMAGE=$(podman inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)

# ── 2. Build new image ────────────────────────────────────────────────────────
log "Building new image..."
podman-compose build "$SERVICE"
success "Image built."

# ── 3. Stop and remove the old container ─────────────────────────────────────
log "Replacing container..."
podman stop "$CONTAINER"  2>/dev/null && podman rm "$CONTAINER" 2>/dev/null || true

# ── 4. Start fresh container from new image ───────────────────────────────────
podman-compose up -d "$SERVICE"
success "Container started."

# ── 5. Delete the old image if it's now dangling ─────────────────────────────
if [[ -n "$OLD_IMAGE" ]]; then
    NEW_IMAGE=$(podman inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)
    if [[ "$OLD_IMAGE" != "$NEW_IMAGE" ]]; then
        log "Removing old image ${OLD_IMAGE:0:20}..."
        podman rmi "$OLD_IMAGE" 2>/dev/null && success "Old image removed." || warn "Old image in use elsewhere — skipped."
    fi
fi

# ── 6. Clean up any other dangling images ────────────────────────────────────
DANGLING=$(podman images -f dangling=true -q)
if [[ -n "$DANGLING" ]]; then
    log "Pruning dangling images..."
    podman rmi $DANGLING 2>/dev/null && success "Dangling images cleaned." || warn "Some dangling images skipped."
fi

# ── 7. Reload nginx config without downtime ───────────────────────────────────
if podman ps --format '{{.Names}}' | grep -q "^${NGINX_CONTAINER}$"; then
    log "Reloading nginx..."
    podman exec "$NGINX_CONTAINER" nginx -s reload && success "Nginx reloaded."
else
    log "Starting nginx..."
    podman-compose up -d nginx && success "Nginx started."
fi

echo ""
success "Rebuild complete — https://10.10.30.160:8443"
