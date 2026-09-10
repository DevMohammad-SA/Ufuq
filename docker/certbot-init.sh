#!/bin/sh
# ---------------------------------------------------------------------------
# One-time issuance of the first Let's Encrypt certificate.
#
# Run this ONCE, manually, on the server, AFTER:
#   1. DNS A record for the domain points at this server (already done).
#   2. The stack is up with the BOOTSTRAP docker/nginx/nginx.conf active
#      (HTTP only, serving /.well-known/acme-challenge/) and the site is
#      reachable over plain http://.
#
# After it succeeds, switch Nginx to docker/nginx/nginx-ssl.conf and
# recreate the nginx container (see the deployment guide).
#
# The renewal afterwards is automatic (the `certbot` service in
# docker-compose.yml runs `certbot renew` every 12h). Nginx still needs a
# reload after an actual renewal — see the deployment notes.
# ---------------------------------------------------------------------------
set -e

DOMAIN="rahhal.saqeel.org.sa"
# Let's Encrypt sends expiry warnings here. CONFIRM this is a mailbox the
# project owner actually reads before running. Taken from pyproject.toml
# ([project].authors email); change it here if it should be different.
EMAIL="dev.mohammad@tuta.io"

# --entrypoint certbot is REQUIRED: docker-compose.yml overrides the certbot
# service's entrypoint with the renewal loop, so without this override the
# `certonly ...` arguments below would be swallowed by `/bin/sh -c '...'`.
docker compose run --rm --entrypoint certbot certbot certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

echo
echo "If the line above says 'Successfully received certificate', the cert is"
echo "now in the certbot_certs volume at:"
echo "  /etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
echo "  /etc/letsencrypt/live/${DOMAIN}/privkey.pem"
echo
echo "Next: activate the HTTPS Nginx config:"
echo "  cp docker/nginx/nginx-ssl.conf docker/nginx/nginx.conf"
echo "  docker compose up -d --force-recreate nginx"
