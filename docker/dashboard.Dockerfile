# Build context must be the project root (.) so both dashboard/ and docker/
# subdirectories are reachable by COPY instructions.
#
# In docker-compose.yml:
#   dashboard:
#     build:
#       context: .
#       dockerfile: docker/dashboard.Dockerfile

# ── Stage 1: Node build ───────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /build

# Copy package manifests first — this layer is cached until they change
COPY dashboard/package.json dashboard/package-lock.json ./

# Clean install from lock file
RUN npm ci

# Copy the rest of the dashboard source and compile
COPY dashboard/ .
RUN npm run build

# ── Stage 2: Nginx static server ─────────────────────────────────────────────
FROM nginx:alpine AS runtime

# Copy the compiled SPA assets from the build stage
COPY --from=builder /build/dist /usr/share/nginx/html

# Replace the default nginx config with the SPA-aware config from docker/
COPY docker/dashboard-nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
