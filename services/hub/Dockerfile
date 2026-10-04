FROM nginx:1.28-alpine-slim@sha256:b33eedfdf089be1f83759ced27b4deec5b6f1b6fc2a6819ebce0ae351a4406e5

# Pull in OS security fixes the pinned base image doesn't have yet. This
# makes the build less reproducible, which the provenance attestation records.
RUN apk upgrade --no-cache

COPY index.html /usr/share/nginx/html/index.html
COPY assets/ /usr/share/nginx/html/assets/
