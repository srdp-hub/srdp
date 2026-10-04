FROM nginx:1.28-alpine-slim

# Pull in OS security fixes the base image doesn't have yet.
RUN apk upgrade --no-cache

COPY index.html /usr/share/nginx/html/index.html
COPY assets/ /usr/share/nginx/html/assets/
