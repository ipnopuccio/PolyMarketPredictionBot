#!/usr/bin/env bash
# healthcheck.sh — Quick status check for all btc-bot-v2 services
# Usage: ./scripts/healthcheck.sh [API_KEY]
set -euo pipefail

API_KEY="${1:-${API_KEY:-}}"
BASE="${BOT_URL:-http://localhost:5003}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}[OK]${NC} $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; ERRORS=$((ERRORS+1)); }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }

ERRORS=0

echo "=== btc-bot-v2 Health Check ==="
echo ""

# 1. Bot API (public endpoint, no auth)
echo "Bot API:"
if curl -sf "$BASE/api/overview" > /dev/null 2>&1; then
    pass "API reachable at $BASE/api/overview"
else
    fail "API not reachable at $BASE"
fi

# 2. Health endpoint (auth required)
if [ -n "$API_KEY" ]; then
    echo ""
    echo "Health endpoint:"
    HEALTH=$(curl -sf -H "X-API-Key: $API_KEY" "$BASE/api/v2/health" 2>/dev/null) || true
    if [ -n "$HEALTH" ]; then
        pass "Health endpoint responding"
        echo "    $HEALTH" | python3 -m json.tool 2>/dev/null || echo "    $HEALTH"
    else
        fail "Health endpoint not responding (check API key)"
    fi
fi

# 3. Docker containers (if docker is available)
if command -v docker &>/dev/null; then
    echo ""
    echo "Docker containers:"
    for svc in bot dashboard postgres nginx; do
        STATUS=$(docker compose ps --format '{{.Status}}' "$svc" 2>/dev/null || echo "")
        if echo "$STATUS" | grep -qi "up"; then
            pass "$svc: $STATUS"
        elif [ -z "$STATUS" ]; then
            warn "$svc: not found (not running via compose?)"
        else
            fail "$svc: $STATUS"
        fi
    done
fi

# 4. PostgreSQL (if pg_isready is available)
if command -v pg_isready &>/dev/null; then
    echo ""
    echo "PostgreSQL:"
    if pg_isready -h "${PG_HOST:-localhost}" -p "${PG_PORT:-5432}" -U tradingbot -d trading_bot -q 2>/dev/null; then
        pass "PostgreSQL accepting connections"
    else
        fail "PostgreSQL not ready"
    fi
fi

echo ""
if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}$ERRORS check(s) failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All checks passed.${NC}"
fi
