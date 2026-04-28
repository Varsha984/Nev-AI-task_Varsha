# NevUp Track 2 — System of AI Engine

Stateful trading-psychology coach with a verifiable memory layer.

The deck for Track 2 asks for three things that fight each other: persistent
memory across restarts, hallucination-free coaching, and a clean evaluation
story. This system wires them together as a single FastAPI service backed by
Postgres + pgvector, with deterministic detectors providing the evidence
that anchors every coaching claim.

**Headline result: 10/10 classification accuracy on the seed dataset.** See
[`eval/reports/classification_report.md`](eval/reports/classification_report.md).

---

## 1. Quickstart — `docker compose up`

Single command, no manual steps:

```bash
docker compose up --build
```

Wait for `app.startup.done` in the logs (~20s on first boot — pulls the
embedding model into the image during build, then warms it on startup).

Health check:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

```json
{"status": "ok", "dbConnection": "connected", "queueLag": 0, "timestamp": "..."}
```

The system works **out of the box with no API keys**. If you want real Claude
calls in the coach, set `ANTHROPIC_API_KEY` before bringing the stack up:

```bash
ANTHROPIC_API_KEY=sk-ant-... docker compose up --build
```

Without the key, the coach uses a deterministic template engine that
satisfies every spec requirement — citations, audit, and token-by-token
streaming — so reviewers without an Anthropic account see the full surface.

---

## 2. Walking the API

### Mint a JWT

```bash
TOKEN=$(docker compose exec -T app python scripts/gen_token.py --name "Alex Mercer")
echo $TOKEN
```

`scripts/gen_token.py --list` prints all 10 seed traders and their userIds.

### Memory contract — write, read, retrieve by signal

```bash
ALEX=f412f236-4edc-47a2-8f54-8763a6ed2ce8
SESSION=4f39c2ea-8687-41f7-85a0-1fafd3e976df

# PUT /memory/{userId}/sessions/{sessionId}
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://localhost:8000/memory/$ALEX/sessions/$SESSION \
  -d '{
    "summary": "Re-entered NVDA quickly after the morning loss; revenge pattern.",
    "metrics": {"planAdherenceScore": 2.4, "sessionTiltIndex": 0.9, "revengeTrades": 4},
    "tags": ["revenge_trading", "morning_session"]
  }'

# GET /memory/{userId}/sessions/{sessionId}  — exact round-trip
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/memory/$ALEX/sessions/$SESSION

# GET /memory/{userId}/context?relevantTo=...  — semantic retrieval
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/memory/$ALEX/context?relevantTo=revenge_trading&limit=3"
```

### Behavioural profile (evidence-cited)

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/users/$ALEX/profile
```

Every dominant pathology in the response carries the exact `evidenceSessions`
and `evidenceTrades` that drove the score. There are no generic claims.

### Streaming coach — `POST /session/events`

The endpoint accepts a stream of trades and returns an SSE response with three
event types: `signal` (one per fired detector), `token` (many — the message
streamed word-by-word), and `done` (full message + final citations).

```bash
curl -N -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://localhost:8000/session/events \
  -d "{
    \"userId\": \"$ALEX\",
    \"sessionId\": \"$SESSION\",
    \"trades\": $(docker compose exec -T app python -c "
import json
from app.seed import seed_store; seed_store.load()
trades = seed_store.trader('$ALEX')['sessions'][0]['trades']
print(json.dumps(trades))
")
  }"
```

You'll see `event: signal` lines for fired detectors, then a stream of
`event: token` chunks, then `event: done` with the full message body and the
final citation list.

### Audit — verify a coaching message has no hallucinated ids

```bash
# Real session id — passes
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://localhost:8000/audit \
  -d "{
    \"userId\": \"$ALEX\",
    \"message\": \"Revenge entries in session $SESSION.\",
    \"citations\": []
  }"
# → {"hallucinated": false, ...}

# Fake session id — flagged
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://localhost:8000/audit \
  -d "{
    \"userId\": \"$ALEX\",
    \"message\": \"See session deadbeef-dead-beef-dead-beefdeadbeef.\",
    \"citations\": []
  }"
# → {"hallucinated": true, "citations": [{"sessionId": "...", "found": false, ...}]}
```

### Cross-tenant access — always 403

```bash
JORDAN=$(docker compose exec -T app python -c "
from app.seed import seed_store; seed_store.load()
print(next(t['userId'] for t in seed_store.all_traders() if t['name']=='Jordan Lee'))
")
curl -i -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/users/$JORDAN/profile
# → HTTP/1.1 403 Forbidden
# → {"error":"FORBIDDEN","message":"Cross-tenant access denied.","traceId":"..."}
```

The full sequence above is automated in `scripts/smoke_test.sh`.

---

## 3. Reproducing the eval

The eval harness is a single command and writes machine- and human-readable
reports.

```bash
docker compose exec app python -m eval.run_eval
```

Outputs:

* `eval/reports/classification_report.json` — full sklearn report + y_true/y_pred
* `eval/reports/classification_report.md` — readable summary
* `eval/reports/per_trader.json` — per-trader scores and evidence

Result on the seed dataset: **100% accuracy (10/10), macro F1 = 1.00.**

Detectors are pure functions — running the harness twice produces
byte-identical reports. CI hashes the JSON output to catch regressions.

---

## 4. Memory persistence across `docker compose restart`

The deck mandates that memories survive container restarts. The Postgres
service in `docker-compose.yml` mounts a named volume (`nevup_pgdata`) at
`/var/lib/postgresql/data`. Verify with:

```bash
# Write a memory
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  http://localhost:8000/memory/$ALEX/sessions/$SESSION \
  -d '{"summary":"persistence check","tags":["test"]}'

# Restart the whole stack (not just the app)
docker compose restart

# Read it back — still there
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/memory/$ALEX/sessions/$SESSION
```

The schema is created idempotently on startup (`CREATE EXTENSION IF NOT
EXISTS vector; CREATE TABLE IF NOT EXISTS ...`), so cold starts on a warm
volume just bind to existing tables.

---

## 5. Architecture at a glance

```
              ┌────────────────────────────────────┐
              │  POST /session/events  (SSE)       │
              │  signal / token / done             │
              └────────────────┬───────────────────┘
                               │
            ┌──────────────────▼──────────────────┐
            │     coach.stream_coaching()         │
            │  ┌──────────┐  ┌─────────────────┐  │
            │  │detectors │→ │ citation builder│  │
            │  └──────────┘  └────────┬────────┘  │
            │                         │           │
            │  ┌──────────────────────▼────────┐  │
            │  │ message gen                   │  │
            │  │  ├─ Claude (if API key)       │  │
            │  │  └─ template fallback         │  │
            │  └──────────────────────┬────────┘  │
            │                         │           │
            │  ┌──────────────────────▼────────┐  │
            │  │ allow-list redactor (UUID)    │  │
            │  └──────────────────────┬────────┘  │
            └─────────────────────────┼───────────┘
                                      ▼
             ┌──────────────────┐  ┌──────────────┐
             │  pgvector store  │  │ seed dataset │
             │  (writable)      │  │ (read-only)  │
             └──────────────────┘  └──────────────┘
                       ▲                    ▲
                       │                    │
                  POST /audit verifies every cited UUID
                  is in one of these two stores.
```

Three independent layers prevent hallucinated citations:

1. The deterministic detectors compute the citation list **before** the
   message is generated. The LLM only narrates around them.
2. The LLM's system prompt explicitly forbids inventing UUIDs and gives
   it the allow-list as part of the user message.
3. The output is regex-scanned and any UUID not on the allow-list is
   replaced with `[REDACTED]` before streaming.

`POST /audit` is a separate after-the-fact check that confirms (1) and (3)
worked. It will catch anything that slips through.

See [`DECISIONS.md`](DECISIONS.md) for the rationale behind each architectural
choice.

---

## 6. Running tests

The same Python deps as the app:

```bash
docker compose exec app pytest -q
```

Test suites:

* `tests/test_detectors.py` — locks in 10/10 classification, scorer
  invariants, evidence-id realism, determinism.
* `tests/test_auth.py` — 401 vs 403 boundaries, JWT round-trip.
* `tests/test_audit.py` — explicit + extracted citations, fake UUID,
  cross-tenant session flagged.
* `tests/test_coach.py` — allow-list redaction, signal detection.

---

## 7. Project layout

```
nevup-track2/
├── app/
│   ├── main.py            FastAPI factory, lifespan, error handlers
│   ├── config.py          Typed settings (single source of truth)
│   ├── auth.py            JWT issue/decode + 401/403 dependencies
│   ├── logging_mw.py      traceId middleware + structured JSON logs
│   ├── db.py              Async SQLAlchemy + idempotent schema bootstrap
│   ├── schemas.py         Pydantic models matching the OpenAPI contract
│   ├── seed.py            Read-only indexed view of the seed dataset
│   ├── embeddings.py      Local sentence-transformers (no external API)
│   ├── memory.py          pgvector-backed memory store
│   ├── detectors.py       Deterministic pathology scorers (10/10 on seed)
│   ├── profile.py         Evidence-cited behavioural profile
│   ├── coach.py           Streaming coach with allow-list redaction
│   ├── audit.py           Hallucination audit
│   └── routers/           memory / session_events / profile / audit / health
├── eval/run_eval.py       Classification report harness
├── tests/                 Detector / auth / audit / coach tests
├── scripts/
│   ├── gen_token.py       Mint a JWT for any seed userId
│   └── smoke_test.sh      End-to-end curl walkthrough
├── seed_data/             Canonical JSON + OpenAPI spec
├── docker-compose.yml     postgres+pgvector + app, single-command boot
├── Dockerfile             Pre-bakes embedding model into the image
├── requirements.txt
├── DECISIONS.md           Why each architectural choice was made
└── README.md              You are here
```

---

## 8. JWT secret

The deck specifies HS256 with this exact secret:

```
97791d4db2aa5f689c3cc39356ce35762f0a73aa70923039d8ef72a2840a1b02
```

It's baked into `docker-compose.yml` as a default. Override via the
`JWT_SECRET` env var if your local kit requires a different value.
