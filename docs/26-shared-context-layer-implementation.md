# 26 · Shared Context Layer — Implementation Plan

**Status:** implementation plan (Jun 2026). Design is locked in
[`24-agent-operating-model.md`](./24-agent-operating-model.md) §24.0.A (Shared
Context Graph) and §24.0.B (Context Intelligence). This document is the concrete
build plan: storage model, backend service, real-time transport, reactive UI,
phasing, and the invariants that make it robust.

**Scope of first delivery:** a single per-run graph (a Skill Build Run) wired
end-to-end — append-only events in Lakebase, a materialized projection, a
read/stream API, and a reactive UI lens. The same substrate generalizes to
usecase-execution graphs and long-term graphs later; the skill-building agents
are the first producers, not the boundary of the concept (see §24.0.A).

---

## §26.1 Design recap (the locked decisions)

1. **Lakebase Postgres is the live source of truth** for context events,
   current node/edge projections, and lifecycle state.
2. **Skill body vs. skill state — hard line** (§24.0.A): Lakebase stores
   lifecycle/graph state and *pointers*; skill source bodies live in Git
   (`repo:/skills/`, `repo:/skill-packs/<partner>/`); drafts in UC Volumes;
   curated summaries in Vector Search. Lakebase never stores skill code.
3. **"Shared" = three scopes, one substrate** (§24.0.A): Long-Term
   (workspace-wide), Shared Working (per-run, multi-agent + reviewer + UI),
   Reasoning (per-run, retained). Scoping differs only by `graph_id`.

---

## §26.2 Robustness model: event sourcing

The event log is the source of truth. The graph (nodes/edges) is a derived
projection. In-memory copies are disposable caches of the projection.

**Invariants:**

1. Events are immutable and append-only; nothing else is the truth.
2. The event insert and the projection update happen in **one Postgres
   transaction** (atomic — both land or neither).
3. The projection is always rebuildable from events (`TRUNCATE` + replay).
4. Any in-memory graph is a per-operation read snapshot, never a write-back
   target. Writes go only through `append_event`.
5. A DB-assigned `sequence_no` (`BIGSERIAL`) orders everything and drives both
   SSE resume and cache-staleness checks. Never order by wall-clock.
6. Idempotency keys (`UNIQUE`) give exactly-once; concurrent appends never lock
   (separate rows); contradictions become `conflict_detected` /
   `conflict_resolved` events rather than silent overwrites.

```
 agent step
   ├─ load_graph(graph_id) → in-RAM read snapshot (ephemeral)
   ├─ reason ...
   └─ append_event(...) ─► ONE txn { INSERT event(seq=N); UPSERT projection; NOTIFY }
                                              │
        rebuild_projection() ◄── replay events (safety net)
                                              │
   UI / other agents ◄── read projection (+ seq-keyed cache) ◄── SSE on sequence_no
```

---

## §26.3 Lakebase schema (created on first write)

DDL follows the repo's existing `CREATE TABLE IF NOT EXISTS` convention
(`evaluation_events.py`, `model_invocation_ledger.py`) — no migration
framework. Tables are additive and namespaced; they cannot affect existing
objects. Schema = `BV_SCHEMA` (Postgres schema mirrors the UC schema name).

```sql
-- Truth: append-only event ledger
CREATE TABLE IF NOT EXISTS shared_context_events (
  sequence_no       BIGSERIAL PRIMARY KEY,        -- DB-assigned ordering authority
  event_id          TEXT NOT NULL,
  graph_id          TEXT NOT NULL,                -- skill_build:sbr_... | outcome_exec:... | long-term id
  layer             TEXT NOT NULL,                -- long_term | working | reasoning
  event_type        TEXT NOT NULL,
  subject           TEXT,
  predicate         TEXT,
  object_ref        TEXT,
  value_json        JSONB NOT NULL DEFAULT '{}',
  actor_type        TEXT NOT NULL,                -- agent | skill | tool | user | system
  actor_id          TEXT NOT NULL,
  trust_level       TEXT NOT NULL DEFAULT 'working', -- working | shared | published | verified
  evidence_refs_json JSONB NOT NULL DEFAULT '[]',
  idempotency_key   TEXT UNIQUE,                  -- exactly-once on retry/replay
  causation_id      TEXT,                         -- event that caused this one
  correlation_id    TEXT,                         -- groups a logical operation
  created_at_ms     BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sce_graph_seq ON shared_context_events (graph_id, sequence_no);

-- Projection: current nodes (derived, disposable, rebuildable)
CREATE TABLE IF NOT EXISTS shared_context_nodes_current (
  graph_id      TEXT NOT NULL,
  node_ref      TEXT NOT NULL,
  layer         TEXT NOT NULL,
  node_type     TEXT,
  label         TEXT,
  value_json    JSONB NOT NULL DEFAULT '{}',
  trust_level   TEXT NOT NULL DEFAULT 'working',
  last_sequence_no BIGINT NOT NULL,
  updated_at_ms BIGINT NOT NULL,
  PRIMARY KEY (graph_id, node_ref)
);

-- Projection: current edges
CREATE TABLE IF NOT EXISTS shared_context_edges_current (
  graph_id      TEXT NOT NULL,
  subject       TEXT NOT NULL,
  predicate     TEXT NOT NULL,
  object_ref    TEXT NOT NULL,
  value_json    JSONB NOT NULL DEFAULT '{}',
  trust_level   TEXT NOT NULL DEFAULT 'working',
  last_sequence_no BIGINT NOT NULL,
  updated_at_ms BIGINT NOT NULL,
  PRIMARY KEY (graph_id, subject, predicate, object_ref)
);
```

`skill_requests` (Phase 2) and `context_packs` (Phase 4) are defined when their
phases land.

---

## §26.4 New infrastructure: a writable Lakebase path

Today the Console API only **reads** Lakebase, and the connection is opened
`autocommit=True, read_only` (`lakebase.py`). The Shared Context Graph is the
first component **born in Lakebase** (written directly, not synced from UC
Delta). Phase 1 therefore adds, in `lakebase.py`:

- A **transactional, writable** connection helper (separate from the read-only
  pooled connection), so `INSERT event` + `UPSERT projection` commit atomically.
- A `lakebase_writable()` context manager that yields a connection with
  `autocommit=False` and commits/rolls back as a unit.

**Pre-build validation (recommended):** a one-shot, safe write/DDL probe
(`CREATE TABLE IF NOT EXISTS bv_write_probe; INSERT; SELECT; DROP`) to confirm
the Lakebase role can create tables and write rows on the `production` branch.
The app authenticates as the workspace user (admin), so this is expected to
pass, but writes are currently unexercised.

---

## §26.5 Backend service (`shared_context_service.py`)

A thin store interface with two implementations: an in-memory store for unit
tests/dev (a list of events + dict projection — also the perfect test of the
fold logic) and a Lakebase store (create-on-first-write).

```python
class SharedContextStore(Protocol):
    def append_event(self, *, graph_id, layer, event_type, subject, predicate,
                     object_ref, value, actor_type, actor_id, trust_level,
                     evidence_refs, idempotency_key=None,
                     causation_id=None, correlation_id=None) -> dict: ...
    def read_events(self, *, graph_id, after_sequence_no=0, limit=500) -> list[dict]: ...
    def load_graph(self, *, graph_id) -> dict:           # {nodes, edges, last_sequence_no}
        ...
    def get_summary(self, *, graph_id) -> dict:          # per-layer counts, trust distribution
        ...
    def get_node_neighborhood(self, *, graph_id, node_ref, depth=1) -> dict: ...
    def rebuild_projection(self, *, graph_id) -> int:    # replay → projection (safety net)
        ...
```

`append_event` (Lakebase impl) is the only write path:

```
with lakebase_writable() as conn:        # one transaction
    insert event (DB assigns sequence_no)
    upsert nodes_current / edges_current from the event
    NOTIFY ctx_<graph_id>, <sequence_no>
# commit
```

The fold (`event → projection mutation`) is a pure function shared by both store
impls and mirrored in the frontend reducer (§26.7).

---

## §26.6 Read + stream API (`routers/context.py`)

Registered in `main.py` alongside the existing routers.

| Endpoint | Purpose |
|---|---|
| `GET /api/context/{graph_id}/summary` | per-layer counts, trust distribution |
| `GET /api/context/{graph_id}/graph` | current projection snapshot `{nodes, edges, last_sequence_no}` |
| `GET /api/context/{graph_id}/events?after_sequence_no=` | delta fetch (also the polling fallback) |
| `GET /api/context/{graph_id}/nodes/{node_ref}/neighborhood` | scoped subgraph for the node panel |
| `GET /api/context/{graph_id}/stream` | **SSE** stream |

**SSE contract (the core of real-time):**

- Each SSE message sets `id: <sequence_no>` and `data: <event json>`.
- The browser's `EventSource` auto-reconnects and sends `Last-Event-ID`; the
  handler resumes with `read_events(after_sequence_no=Last-Event-ID)`. Missed
  events are recovered for free — no gaps, no full reload.
- The handler wakes on Postgres `LISTEN ctx_<graph_id>` (no busy-polling). If
  `LISTEN/NOTIFY` is awkward under Databricks Apps pooling, fall back to a
  single shared poller per `graph_id` that fans out to all SSE clients (O(1) DB
  load regardless of client count).
- Events are emitted only from the durable `sequence_no` (write → DB assigns seq
  → notify → read back → push). The UI never sees a fact that isn't durable.

No new Python dependencies (FastAPI streams SSE natively; Lakebase is Postgres).

---

## §26.7 Reactive UI

Uses only existing `apps/console` dependencies: TanStack Query, Zustand, React
Flow, `@tanstack/react-virtual`, and the native `EventSource`. **No new npm
packages.** The browser store holds a disposable derived view; reliability rests
entirely on the backend invariants (§26.2), so the state holder is replaceable.

**Pipeline:**

1. **Initial snapshot** via TanStack Query: `GET /graph` → `{nodes, edges,
   last_sequence_no}`. No history replay in the browser.
2. **Live deltas** via `EventSource(/stream?after=<last_sequence_no>)`. Each
   event runs through a **pure reducer** (`applyEvent(graph, event) → graph'`) —
   the same fold as the backend projection — updating a Zustand graph slice
   `{nodes, edges, lastSequenceNo}`.
3. **Lenses** (the "no raw hairball" visibility rule). All read the same Zustand
   slice via selectors, so they update together and consistently:
   - **Workflow stepper** — working-layer task nodes as a vertical stepper
     (primary view).
   - **React Flow canvas** — `reactflow` fed from the slice; node id-diffing
     means one event animates one node.
   - **Event timeline** — virtualized raw event list.
   - **Node context panel** — `neighborhood(node_ref)` on click.
   - **Decision-trace lens** — reasoning-layer events.
4. **Optimistic human actions**: the reviewer's action applies a provisional
   event locally (temp `idempotency_key`), POSTs `append_event`; the durable
   echo arrives via SSE and replaces the provisional, matched by
   `idempotency_key`. Safe double-apply prevention.

Reconnect resumes from `Last-Event-ID`; the browser graph is always
`snapshot ⊕ folded(deltas)`. If SSE is proxy-blocked, the same
`events?after_sequence_no=` endpoint backs a polling fallback with identical UI
code.

---

## §26.8 Phases

| Phase | Deliverable | Notes |
|---|---|---|
| **1** | Writable Lakebase path + `shared_context_service` (store interface, in-memory + Lakebase impls) + events/projection schema + `routers/context.py` read API | Additive; generic over `graph_id`. Unit-tested via in-memory store. |
| **1b** | SSE stream endpoint + `LISTEN/NOTIFY` | Streaming sibling of the `events` query. |
| **2** | `skill_requests` table; replace hardcoded `_skill_gaps` in `skill_builder_service.py`; emit context events per Skill Build lifecycle action | First real producer into a `skill_build:sbr_...` graph. |
| **3** | Reactive UI lenses (workflow stepper first, then React Flow canvas, timeline, node panel) | TanStack Query snapshot + SSE reducer + Zustand. |
| **4** | Context Pack Builder (Hot/Warm/Cold) + `context_packs` table | First Context Intelligence slice (§24.0.B). |
| **5** | Real multi-agent harness (Planner/Evidence/Runtime/Test/Reviewer) on the graph | Depends on the Skill Executor. |

**Smallest valuable first PR:** Phase 1 (+1b) — schema + service + read/stream
API. Additive, independently testable, and reused unchanged by Phase 2 and by
later usecase-execution graphs.

---

## §26.9 Reliability summary

| Property | Guaranteed by |
|---|---|
| Truth survives / never lost | Lakebase append-only event log (Postgres ACID) |
| Rebuild after corruption | Replay events → projection |
| No missed events on disconnect | `sequence_no` + SSE `Last-Event-ID` resume |
| Exactly-once / no double-apply | Idempotency keys (`UNIQUE`) |
| Deterministic ordering | DB-assigned `sequence_no` |
| Atomic event + projection | Single Postgres transaction |
| UI consistency | Frontend reducer folds the same events as the backend projection |

The frontend (Zustand/React Flow) renders a replaceable projection of the
durable log; no critical state lives in the browser.
