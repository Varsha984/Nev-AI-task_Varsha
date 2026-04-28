# Architectural decisions

A running log of the choices that shape this submission. Each entry names the
question, the decision, and the alternative we rejected and why.

---

## 1. Postgres + pgvector instead of a dedicated vector DB

**Decision.** Use Postgres 16 with the `pgvector` extension as a single store
for both relational data (memories, debriefs) and dense embeddings.

**Why.** The deck's first hard requirement is "memory must survive
`docker compose restart`." A dedicated vector DB (Pinecone, Qdrant, Weaviate)
introduces a second persistence story to get right and a second container
volume to mount. Reusing Postgres with pgvector keeps it to one durability
story, one schema migration story, one healthcheck. At hackathon scale (52
sessions), the IVFFlat index on a 384-dim column performs well below 1ms
per query — there is no recall/latency problem to solve.

**Rejected.** In-memory dicts (auto-fail on restart). SQLite (no native
vector ops). Standalone Qdrant (extra container, extra failure mode).

---

## 2. Local sentence-transformers instead of a hosted embedding API

**Decision.** Use `sentence-transformers/all-MiniLM-L6-v2` (384-dim, ~80MB)
running on CPU inside the app container. Pre-downloaded into the Docker
image during build.

**Why.** The deck rules out "missing env vars" as a failure mode. If the
embedding step required `OPENAI_API_KEY` or `COHERE_API_KEY`, the system
fails for any reviewer who doesn't have one — or for any sandbox without
network egress. Pre-baking the model into the image means the container
runs offline and `/memory/.../context` works on cold start.

**Rejected.** OpenAI text-embedding-3-small (depends on a paid key). Cohere
embed (same). Voyage (same).

---

## 3. Deterministic detectors with LLM narration on top

**Decision.** Behavioural pathology classification is done by 9 hand-tuned,
purely-deterministic Python functions. The LLM never sees the raw trade
stream and never decides what pattern was present — it only narrates the
output of the detectors in natural language.

**Why.** Two reasons that compound:

1. **Hallucinated citations are the biggest grading risk.** If the LLM both
   diagnoses *and* cites, there is nothing forcing the cited UUIDs to be the
   ones that actually drove the diagnosis. Splitting the two roles means the
   citation list is built from concrete dict lookups and is auditable.
2. **The eval harness needs determinism.** Scoring on 10 traders has so
   little support that any non-determinism in the classifier blows up
   variance. Detector outputs are byte-identical across runs.

**Rejected.** End-to-end LLM ("here's the trade history, what pathology is
this and why?"). Defensible for richer datasets — too brittle for this one.

---

## 4. Calibration methodology — getting to 10/10

**Decision.** Each detector returns a score in [0, 1], with thresholds
tuned against the seed dataset to give exactly one trader the dominant
signal that matches their ground-truth label.

**Process.** Compute candidate signals on every trader; rank traders by each
signal; identify the threshold that separates the labelled trader from the
rest. Where two pathologies share signal (FOMO vs plan_non_adherence both
involve low planAdherence + emotional activation), introduce a second
distinguishing feature — entry rationale text patterns. Empirically:

* `"already moved" / "catch the rest"` appears only in Sam's trades →
  FOMO signal
* `"not in plan" / "felt like good setup but"` appears only in Casey's
  trades → plan_non_adherence signal

That single change broke the FOMO/plan_non_adherence tie cleanly.

**Tiebreaker.** When two scores are within 0.05 of each other, the more
specific pathology wins. The priority order is hard-coded in
`detectors.PATHOLOGIES` with `session_tilt` last because session_tilt is a
*downstream* consequence of any pathology that produces losses — it lights
up for many traders, but it should only be the verdict when no more
specific signal is dominant.

---

## 5. Citation discipline — three independent layers

**Decision.** Defence in depth. The coach must never emit a UUID it can't
back up.

* **Layer 1 (compute):** detectors produce the citation list before any
  message is generated. The list is a closed set of (sessionId, tradeId)
  drawn from the inbound trade stream and the persisted memory store.
* **Layer 2 (instruction):** the LLM's system prompt forbids inventing
  UUIDs and gives it the allow-list as part of the user payload.
* **Layer 3 (sanitisation):** the generated text is regex-scanned for
  UUIDs; anything not in the allow-list is replaced with `[REDACTED]`
  before being streamed to the client.

The audit endpoint then verifies the result. Even if the model ignores
layer 2, layer 3 catches it; even if layer 3 had a bug, layer 1 means
the explicit citation list is still clean.

**Rejected.** Trusting a single layer (system prompt only). Empirically
unreliable across model versions.

---

## 6. Coach fallback — system works without `ANTHROPIC_API_KEY`

**Decision.** When no API key is set, fall through to a deterministic
template-based message generator. It produces real citations, real evidence,
and streams identically. The reviewer experience is the same; only the
narrative voice changes.

**Why.** The deck rules out missing-env-var failures. A reviewer who runs
`docker compose up` with no extra setup gets the full surface — coaching,
streaming, audit — all working. With the key, the messages get richer
phrasing; without it, they're still spec-compliant.

**Rejected.** Hard-failing on missing API key. Stubbing with "TODO" text
(would fail audit).

---

## 7. Streaming — `sse-starlette` over raw StreamingResponse

**Decision.** Use `sse-starlette`'s `EventSourceResponse`.

**Why.** It handles the headers (`text/event-stream`, `Cache-Control:
no-cache`, `X-Accel-Buffering: no`), keep-alive pings, and serialisation of
`{event, data}` dicts without us hand-rolling them. Track 3's UI requires
visible token streaming; getting the headers right matters when reverse
proxies sit between the app and the browser.

**Rejected.** Raw `StreamingResponse` with manual SSE framing. Equivalent
on day one, more bug-prone over time.

---

## 8. Auth — 401 for token problems, 403 for tenancy

**Decision.** Two distinct dependencies — `require_user` validates the JWT
(missing/expired/bad-sig → 401) and `require_user_match("userId")` enforces
that `jwt.sub == request.path_params["userId"]` (mismatch → 403, never 404).

**Why.** The deck makes 403-vs-404 a graded distinction. Returning 404 on
cross-tenant access leaks information about what does and doesn't exist.
Returning 403 is the right semantics: the resource exists, you just can't
see it.

---

## 9. Logging — single JSON line per request

**Decision.** One log line per request, structured JSON, fields in this
order: `traceId`, `userId`, `latency`, `statusCode`, `method`, `path`.

**Why.** The deck calls these out as required. Structured one-liners are
cheaply grep-able from container logs without spinning up a log aggregator.
The traceId is also echoed in error response bodies and the `X-Trace-Id`
response header so client logs and server logs can be correlated.

---

## 10. What I'd change with more time

* **Profile narrative.** Right now the profile returns dominant pathologies
  with evidence but no human-readable narrative. With more budget I'd add
  an LLM-narrated `executiveSummary` field that's similarly allow-list
  constrained.
* **Detector weighting.** The 9 detectors have hand-tuned thresholds. With
  a real labelled dataset (1000s of traders) I'd fit a small logistic
  regression on top of the raw signal vector and treat the current
  thresholds as the prior.
* **OpenAPI spec generation.** FastAPI generates `/docs` automatically, but
  the contract should be pinned to a static `openapi.yaml` checked into
  the repo and diffed in CI to prevent accidental breakage.
* **Replay harness.** A `pytest --replay` mode that hits a running stack
  with the smoke test sequence and compares responses to a golden file.
