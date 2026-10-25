# Stage 1: Build the static site using the official Quarto image
FROM ghcr.io/quarto-dev/quarto:1.8.24@sha256:866dead91b280186bc9de568a084daa7893f7e93ba528794b94bd8998b1f32cc AS builder

# Copy the Quarto source file into the container
WORKDIR /app
COPY reports/index.qmd .
COPY reports/_quarto.yml .

# Render the .qmd file into an HTML site in the _site directory
RUN quarto render

# ---

# Stage 2: Serve the static files using a hardened Nginx image
FROM dhi.io/nginx:1.30@sha256:8a59b132d68c44e8b4e463a5239eea0ab6d81d4982de07eb921bd1f77ea4b1ee

# Copy the built static site from the 'builder' stage
COPY --from=builder /app/_site /usr/share/nginx/html
