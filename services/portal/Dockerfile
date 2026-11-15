FROM dhi.io/nginx:1.30@sha256:8a59b132d68c44e8b4e463a5239eea0ab6d81d4982de07eb921bd1f77ea4b1ee

# The hardened image runs nginx as uid 65532, so it listens on 8080 instead of 80.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public /usr/share/nginx/html
