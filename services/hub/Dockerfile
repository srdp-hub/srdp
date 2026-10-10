FROM nginx:1.31-alpine-slim@sha256:f761b94f2cb9e8e05e2943d5f773609596113ef69b54e2433a996d109a8f78b7

# Pull in OS security fixes the pinned base image doesn't have yet. This
# makes the build less reproducible, which the provenance attestation records.
RUN apk upgrade --no-cache

COPY index.html /usr/share/nginx/html/index.html
COPY assets/ /usr/share/nginx/html/assets/
