# ── Stage 1: dependency installer ───────────────────────────────────────────
FROM python:3.11-slim AS builder

# System deps needed to compile certain pip packages (e.g. numpy C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only the project manifest first so this layer is cached when only
# source code changes.
COPY pyproject.toml .

# Install all runtime dependencies into an isolated prefix so we can copy
# just the site-packages into the final image.
RUN pip install --no-cache-dir --prefix=/install ".[dev]" \
    || pip install --no-cache-dir --prefix=/install .

# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Install curl (needed for the HEALTHCHECK command) and no other extras
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN groupadd --gid 1001 botuser \
    && useradd --uid 1001 --gid botuser --shell /bin/bash --create-home botuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source tree
COPY src/ ./src/

# Create log directory owned by the non-root user
RUN mkdir -p /app/logs && chown -R botuser:botuser /app

USER botuser

# Make `python -m bot` resolve correctly
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Port the API listens on (mirrors DASHBOARD_PORT default)
EXPOSE 5003

# Health check hits the public overview endpoint (no auth required)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5003/api/overview || exit 1

# Default command: run all bots + dashboard.
# Override with e.g.  command: ["python", "-m", "bot", "--status"]
# or for API-only mode: command: ["python", "-m", "uvicorn", "bot.dashboard.app:create_app", "--host", "0.0.0.0", "--port", "5003"]
CMD ["python", "-m", "bot"]
