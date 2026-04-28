#!/usr/bin/env bash
# End-to-end smoke test. Assumes the stack is up: `docker compose up -d`.
# Walks the entire memory contract → coach stream → audit pipeline against
# Alex Mercer (the revenge_trading trader), and prints PASS/FAIL for each step.
#
# Run:  bash scripts/smoke_test.sh
#       (or after `docker compose up -d`)
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ALEX_USER_ID="f412f236-4edc-47a2-8f54-8763a6ed2ce8"
ALEX_SESSION_ID="4f39c2ea-8687-41f7-85a0-1fafd3e976df"
ALEX_TRADE_ID="9c967550-357f-4bfb-9726-c8b863e968ce"

echo "==> Health"
curl -fsS "$BASE_URL/health" | python3 -m json.tool

echo
echo "==> Mint token for Alex"
TOKEN=$(docker compose exec -T app python scripts/gen_token.py --name "Alex Mercer")
[ -n "$TOKEN" ] || { echo "FAIL: no token"; exit 1; }
echo "Token (truncated): ${TOKEN:0:40}..."

echo
echo "==> PUT /memory/{userId}/sessions/{sessionId}"
curl -fsS -X PUT \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "$BASE_URL/memory/$ALEX_USER_ID/sessions/$ALEX_SESSION_ID" \
    -d '{
      "summary": "Re-entered NVDA quickly after the morning loss; revenge pattern.",
      "metrics": {"planAdherenceScore": 2.4, "sessionTiltIndex": 0.9, "revengeTrades": 4},
      "tags": ["revenge_trading", "morning_session"]
    }' | python3 -m json.tool

echo
echo "==> GET /memory/{userId}/sessions/{sessionId}  (round-trip check)"
curl -fsS \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/memory/$ALEX_USER_ID/sessions/$ALEX_SESSION_ID" | python3 -m json.tool

echo
echo "==> GET /memory/{userId}/context?relevantTo=revenge_trading"
curl -fsS \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/memory/$ALEX_USER_ID/context?relevantTo=revenge_trading&limit=3" \
    | python3 -m json.tool

echo
echo "==> GET /users/{userId}/profile"
curl -fsS \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/users/$ALEX_USER_ID/profile" | python3 -m json.tool

echo
echo "==> POST /audit (real session id, should pass)"
curl -fsS -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "$BASE_URL/audit" \
    -d "{
      \"userId\": \"$ALEX_USER_ID\",
      \"message\": \"Revenge entries in session $ALEX_SESSION_ID, especially trade $ALEX_TRADE_ID.\",
      \"citations\": []
    }" | python3 -m json.tool

echo
echo "==> POST /audit (fake session id, should flag hallucinated)"
curl -fsS -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "$BASE_URL/audit" \
    -d "{
      \"userId\": \"$ALEX_USER_ID\",
      \"message\": \"You did this in session deadbeef-dead-beef-dead-beefdeadbeef.\",
      \"citations\": []
    }" | python3 -m json.tool

echo
echo "==> Cross-tenant 403 check (token for Alex, asking for Jordan's profile)"
JORDAN_USER_ID="$(docker compose exec -T app python -c "
from app.seed import seed_store; seed_store.load()
print(next(t['userId'] for t in seed_store.all_traders() if t['name']=='Jordan Lee'))
")"
HTTP=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/users/$JORDAN_USER_ID/profile")
echo "Got HTTP $HTTP for cross-tenant request (expected 403)"
[ "$HTTP" = "403" ] && echo "PASS: cross-tenant blocked" || { echo "FAIL"; exit 1; }

echo
echo "All smoke checks passed."
