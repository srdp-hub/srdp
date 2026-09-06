# Stage 1: Build the static site using the official Quarto image
FROM ghcr.io/quarto-dev/quarto:1.8.24 AS builder

# Copy the Quarto source file into the container
WORKDIR /app
COPY reports/index.qmd .
COPY reports/_quarto.yml .

# Render the .qmd file into an HTML site in the _site directory
RUN quarto render

# ---

# Stage 2: Serve the static files using a lightweight Nginx image
FROM nginx:1.28-alpine-slim

# Copy the custom Nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy the built static site from the 'builder' stage
COPY --from=builder /app/_site /usr/share/nginx/html
