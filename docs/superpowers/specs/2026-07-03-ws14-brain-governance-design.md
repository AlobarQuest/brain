# WS-1.4 — Brain Governance: columns + propose/approve lifecycle + conflict detection

**Date:** 2026-07-03
**Workstream:** WS-1.4 (software factory, Phase 1 closer) — Deliverable D5
**Owning repo:** `~/Projects/brain` (unified four-brain platform)
**Status:** Design approved by Devon 2026-07-03 (incl. Sub-A = approver `auto_approve`, Sub-B = thoughts `approved/informational`)

## 1. Goal

Give the four brains (app / infra / open / code) the **governed-knowledge record shape** from
companion §3.2 — lifecycle `status`, `authority`, provenance, `owner`, `applicability`,
supersession, `version` — plus:

- a **propose → approve lifecycle**: agents may only *propose*; approval is a **Devon-only**,
  structurally-enforced act;
- **conflict detection**: a same-applicability contradiction against an existing authoritative
  record is **flagged at write** (accept-as-proposed + flag, never reject);
- **safe retrieval**: reads default to approved knowledge and can filter by authority;
- a **migration** of existing records to sensible governance defaults.

Out of scope (do NOT build): review-queue UI, the observation→lesson promotion / correlation
pipeline (WS-6.2), any orchestrator change, wiring the new contributor key to real agents.

## 2. Architecture context (as-built, verified 2026-07-03)

- One codebase; each deployment runs **one brain**, selected by `BRAIN_TYPE`. **One Postgres DB per
  brain** (`appbrain`, `infrabrain`, `openbrain`, code-brain's DB is named `postgres`). All four are
  standalone `pgvector:pg16` on the **Hetzner prod VPS Coolify**; backups via `vps-backup`/restic tag
  `vps-production` (backup hard-gate: **GO**, finding 572 CLOSED).
- Per-brain **Alembic** chains applied at container start (`scripts/start.sh` → `alembic upgrade head`).
  Additive `ADD COLUMN` is the established pattern (see `app/.../0003`).
- **code-brain declares its own isolated `Base`** (its `rules`/`lessons` table names collide with
  infra's) — the governance mixin must be `Base`-agnostic.
- Auth = one Starlette middleware validating a single `mcp_access_key` (`x-brain-key` header or
  `?key=`). MCP tools can read request headers via FastMCP's request-context dependency.
- Read tools hand-build result dicts; code-brain centralizes shapes in `tools/serialize.py`. New
  columns must be surfaced in every read tool dict + `serialize.py` + the REST `/api/*` builders.
- Tests mock the DB / use SQLite in-memory; there is **no real-Postgres or DDL test fixture**, and
  `make check` is referenced by CLAUDE.md but has **no Makefile target** (lint/type run via
  pre-commit + the code-standards Stop hook, and CI `quality.yml`/`ci.yml`).

## 3. Governed tables

Governance applies to **knowledge-record** tables only:

| brain | tables getting governance |
|---|---|
| infra | `rules`, `lessons`, `combos` |
| app | `app_knowledge` |
| open | `thoughts` |
| code | `rules`, `lessons`, `exemplars` |

**Excluded** (operational inventory / organizational catalog, not authoritative knowledge): infra
`versions` (package pins), app `apps` (application registry), **code `roads`** (the paved-road
*catalog* that rules/exemplars attach to — the code-brain analogue of `apps`). `roads` is excluded for
two reinforcing reasons, decided 2026-07-03 during build: (1) principled — it is a registry/catalog,
not an authority-bearing knowledge record; (2) mechanical — `roads` already owns a domain `status`
column (paving state: `paved`/`partial`/`unpaved`/`paving`, with its own CHECK, 51 seeded rows, in
`list_roads`/`road_dict`/`/api/roads`/tests) that collides irreconcilably with the governance
lifecycle `status`. Consequence: `add_road` remains direct-write (not propose-only); `road_dict` does
**not** gain governance fields; the code migration touches only `rules`/`lessons`/`exemplars`;
`register_governance_tools` is wired for `{"rule","lesson","exemplar"}` only.

## 4. Governance columns — `GovernanceMixin`

A `Base`-agnostic declarative mixin in `src/core/governance.py`, mixed into every governed model
(works with both the shared `core.db.Base` and code-brain's isolated `Base`). Columns (per §3.2):

| column | type | server default | meaning |
|---|---|---|---|
| `status` | text NOT NULL | `'proposed'` | `proposed` / `approved` / `deprecated` / `superseded` (CHECK) |
| `authority` | text NOT NULL | `'informational'` | `informational` / `recommended` / `required` (CHECK) |
| `proposed_by` | text NULL | — | WS-1.2 registry actor id that proposed the record |
| `owner` | text NULL | — | owning registry identity / standard slug |
| `reviewed_by` | text NULL | — | approver identity (registry id; Devon) |
| `reviewed_at` | timestamptz NULL | — | approval timestamp |
| `applicability` | JSONB NOT NULL | `'{}'` | structured scope; drives conflict key + consumer filtering |
| `version` | integer NOT NULL | `1` | record version (bumped on material change; future) |
| `conflict_note` | text NULL | — | human-readable flag set on a **proposal** at write (refs matched ids); **never cleared** — preserved as provenance |
| `conflict_kind` | text NULL | — | `duplicate` (Layer 1) or `overlap` (Layer 2), NULL if unflagged; drives the ack gate (CHECK) |
| `conflict_acknowledged_at` | timestamptz NULL | — | set when an approver approves over a `duplicate` flag; note is kept, not nulled |

**Supersession** is **not** in the mixin (self-FK type differs by table): each governed model adds
`supersedes_id` / `superseded_by_id` typed to its own PK (int for infra/code, UUID for app/open).
`app_knowledge` already has `supersedes_id` (UUID) — reuse it; add `superseded_by_id` for symmetry.

CHECK constraints on `status` and `authority` mirror the existing severity-CHECK pattern in the models.

## 5. Lifecycle — propose-only writes + Devon-only approval

### 5.1 Two-tier keys (structural enforcement)

`src/core/config.py` gains an optional `contributor_key: str | None` (validated like
`mcp_access_key`: 64 lowercase hex). Semantics:

- **`mcp_access_key` = approver key.** Unchanged; Devon's existing claude.ai connectors keep working
  and can approve. Fully backward-compatible.
- **`contributor_key` = propose+read only.** Optional; unset today → behavior identical to now.
  Future factory agents get this key.

`src/core/auth.py` middleware accepts **either** key for general access (so contributor callers can
reach propose/read tools), and records which tier matched into the request scope
(`request.state`/`scope["state"]`) — but tools do not rely on state propagation; see 5.2.

### 5.2 `require_approver()` helper (`src/core/governance.py`)

A helper that reads the caller's presented key from the request (FastMCP request-context / headers)
and `hmac.compare_digest`-checks it against `settings.mcp_access_key` (the approver key). Returns
True only for the approver key. Used by every approval-capable action. This is the load-bearing
structural gate: a contributor-key (agent) caller **cannot** approve, regardless of any argument it
passes. If the request/header context is unavailable (non-HTTP path), default **deny**.

> **Plan spike (de-risk first):** confirm FastMCP 3.x exposes the inbound request headers to a tool
> (e.g. `fastmcp.server.dependencies.get_http_request()`/`get_http_headers()`). If it does not, fall
> back to having the auth middleware stash the matched tier in `scope["state"]` and read it in the
> tool, or expose approval only on an approver-key-guarded REST route registered via
> `register_routes(app)`. The lifecycle design does not change; only the plumbing of "which key called"
> does. This is the first task in the plan.

### 5.3 Write tools become propose-only

Every existing write tool (`add_rule`, `add_lesson`, `capture_knowledge`, `onboard_app` knowledge
writes, `add_road`, `add_exemplar`, infra `add_version`/`update_version` remain as-is since `versions`
is excluded) inserts records with `status='proposed'`, `authority='informational'`, `proposed_by`
from the caller (registry id arg, default `'unattributed'`), `applicability` derived from the
record's fields (§6.2). `update_*` tools operate only on the mutable content fields as today; they do
not change `status`/`authority` (those move only through approve/reject).

**Sub-A — approver `auto_approve` convenience:** write tools accept `auto_approve: bool = False`.
When True **and** `require_approver()` passes, the record is created `status='approved'` with
`reviewed_by`/`reviewed_at` stamped in the same call (still runs conflict detection §7). A
contributor-key caller passing `auto_approve=True` is ignored (stays proposed) — the gate is
structural, not argument-trust.

### 5.4 New governance tools (per governed brain)

- `approve(record_type, id, acknowledge_conflict: bool = False)` — approver-gated. proposed→approved,
  stamps `reviewed_by`/`reviewed_at`. If the record carries a **`conflict_kind='duplicate'`** flag that
  is not yet acknowledged (`conflict_acknowledged_at IS NULL`), approval **requires**
  `acknowledge_conflict=True` (else returns `{"error":"conflict_unacknowledged", "conflict_note": ...}`);
  on ack, `conflict_acknowledged_at` is stamped and the **note is preserved** (provenance of a contested
  approval). A `conflict_kind='overlap'` flag is **advisory only** and never blocks approval.
- `reject(record_type, id, reason)` — approver-gated. proposed→deprecated. The reason is **appended**
  to `conflict_note` (prefixed `REJECTED:`), preserving any existing conflict flag rather than
  clobbering it. Idempotent.
- `deprecate(record_type, id)` — approver-gated. approved→deprecated (retires a live record;
  complements the existing `retire_rule`/`delete_*` soft-deletes, which now also set
  `status='deprecated'`).

`record_type` disambiguates within a brain (e.g. code: `road`/`rule`/`lesson`/`exemplar`). These are
registered by each brain's `register()` via a shared factory in `src/core/governance.py` so the four
brains stay consistent.

### 5.5 Existing soft-delete reconciliation

`retire_rule` / `delete_rule` / `delete_knowledge` continue to work and now also set
`status='deprecated'` (keeping `retired_at`/`is_active` in sync). `restore_rule` sets
`status='approved'`. No behavior regressions for current callers.

## 6. Migration & backfill (one Alembic revision per brain)

### 6.1 Structure

Each brain gets one new revision: (a) `ADD COLUMN` for the mixin columns + supersession columns
(additive, `IF NOT EXISTS`-safe pattern), (b) a data-migration `UPDATE` for defaults, (c) `downgrade`
drops the columns. Migrations are idempotent and safe to re-run.

### 6.2 Backfill rules

- `status`: `retired_at IS NOT NULL` (or `is_active = false` for app_knowledge) → **`deprecated`**;
  everything else → **`approved`**.
- `authority`: **`required`** only for the **enumerated scanner/engine-enforced set**, matched on
  stable slug + category (not fragile ids):
  - infra-brain, `category='security'` AND `rule IN ('bws.no-token-in-tracked-files',
    'bws.no-token-in-git-history', 'bws.bootstrap-token-not-inline', 'cred.exposure-rotate')`
    → `required` (verified live 2026-07-03: each has an executable `check` naming a live engine — the
    BWS scanner for the three `bws.*`, infraops security-drift for `cred.exposure-rotate`).
  - All other existing records → **`informational`**. Rationale (Devon-approved, "conservative +
    truthful"): `authority='required'` asserts a live gate enforces the record; BLOCK-severity *prose*
    rules (`check: null`) and rules whose executing gate is unverified (e.g. code-brain id 4's
    `forbidden_pattern`) are **not** claimed as required. They can be promoted later per-record once a
    gate is confirmed. A recommended-tier (WARN + live engine) set is not populated at migration; the
    predicate above is the only promotion.
- `applicability`: backfilled from existing fields into a canonical JSON object (§7.2), so conflict
  detection and consumer filtering have data on day one.
- `proposed_by` = `'migration:ws-1.4'`; `owner` left NULL (unknown for historical records);
  `reviewed_by`/`reviewed_at` set for the `approved` rows to `'migration:ws-1.4'` / migration time.
- `version` = `1`.

### 6.3 Open brain (Sub-B, approved)

`thoughts` get the governance columns for uniformity, but `capture_thought` creates
`status='approved'`, `authority='informational'` (thoughts are observations that make no authority
claim; rapid capture must not require per-thought approval). Open brain has **no** in-place
approve/reject and is **excluded from conflict detection**; in-place promotion to a knowledge brain is
WS-6.2. Existing thoughts backfill to `approved/informational`.

## 7. Conflict detection (reviewed-hard section)

Sharpened after an independent Opus-4.8-max adversarial review (Fable unavailable). The prior draft's
"machine-directive contradiction" layer was unbuildable against the real `check` shapes
(`forbidden_pattern` has no polarity/expected-value axis) and mutated approved target rows; both are
fixed below.

### 7.1 Semantics

At write time (propose, or approver `auto_approve`), a governed record is matched against existing
**approved** records of the **same `record_type`** at authority **`recommended` or `required`** (only
authoritative records are conflict *targets*, keeping noise proportional to authority). Detection runs
**pre-commit**, before the new record is visible, so a record is never its own target. The write
**always succeeds** (accept-as-proposed / accept-and-auto-approve). **Only the new proposal is
flagged** — the matched approved record is **never mutated** (avoids a low-trust proposer writing an
authoritative row, lost-update contention on a scalar note, and audit-provenance muddying).

Two independent layers:

1. **Duplicate (Layer 1 — ack-gated).** The new record carries a `check`, and an approved
   `recommended`/`required` record of the same type carries a **byte-identical normalized `check`**
   (JSON with sorted keys). This means "you are re-adding a directive the scanner already enforces."
   Decidable and low-noise. Sets `conflict_kind='duplicate'`; approving over it requires
   `acknowledge_conflict=True` (§5.4). (Exact-duplicate *prose* rules are already prevented by the
   existing `rule`/`title` UNIQUE constraints — Layer 1 covers the checked-directive case they miss.)
2. **Overlap (Layer 2 — advisory).** The canonical applicability signatures (§7.2) are equal but the
   checks are **not** identical. Signals "another authoritative record governs this scope — review,"
   without claiming contradiction. Sets `conflict_kind='overlap'`; it is **surfaced in the write
   response and stored in `conflict_note`**, but does **not** block approval.

True *contradiction* detection (a `required_pattern` vs `forbidden_pattern` on identical scope, or a
`config_value{expected}` shape with differing expected values) is **deferred** — no check shape in the
corpus has a polarity/expected axis, so shipping it now would be hand-waving. When such a shape is
introduced, add a Layer-1b pair and spec it explicitly.

### 7.2 Canonical applicability signature (per record type)

Conflict detection is scoped to the **record types that can bear a directive and reach
`recommended`/`required`** authority. Lessons, combos, exemplars, roads, and thoughts are **excluded**
from conflict detection (they migrate `informational`, have no directive to duplicate, and no
authoritative targets post-migration — including them would be dead, noisy, or ill-defined).

| record type | Layer-1 duplicate key | Layer-2 overlap signature |
|---|---|---|
| infra `rule` | normalized `check` (skip if `check IS NULL`) | `{category, source_app}` — **skip if `source_app IS NULL`** (bare category is too coarse to flag) |
| code `rule` | normalized `check` (skip if `check` kind=`judgment`/NULL) | `{road_slug, category}` |
| app_knowledge | — (no `check`) | `{app_slug, knowledge_type}` |

The logic lives in `src/core/governance.py` (`normalized_check(record)`, `conflict_key(record)`,
`find_conflicts(session, record)`), parameterized per record type by a small per-brain descriptor, so
the brains share one implementation.

**Live coverage at ship (honest scope):** the migration produces **~4 `required` targets, all infra
`category='security'` rules, and zero `recommended` targets** (§6.2). Therefore `find_conflicts` can
actually fire **only for infra rules** at ship; code-brain and app-brain have **no** authoritative
targets until a future record is approved to `recommended`/`required` there (the only paths that grow
the target pool are `approve`-to-authority and approver `auto_approve` to authority — nothing in this
WS seeds `recommended`). The spec does not imply portfolio-wide live coverage on day one.

### 7.3 Demonstrated test cases (exit criterion)

Demonstrated against a **real migrated `required` rule**, not only a synthetic fixture:

- **Duplicate (Layer 1, ack-gated):** with a migrated approved `required` infra BWS rule in place
  (e.g. `bws.no-token-in-tracked-files`, check `{"kind":"forbidden_pattern","scope":"tracked",
  "pattern":"..."}`), propose a new infra security rule whose normalized `check` is **identical** →
  the new proposal gets `conflict_kind='duplicate'` and a `conflict_note` referencing the matched id;
  the matched rule is **unchanged**; `approve` without `acknowledge_conflict` is **refused**; with it,
  approval succeeds, `conflict_acknowledged_at` is stamped, and the note is **preserved**.
- **Overlap (Layer 2, advisory):** propose a second infra security rule with the **same
  `{category, source_app}`** as an approved `required` rule but a **different** `check` → the proposal
  gets `conflict_kind='overlap'` and a surfaced note, and `approve` (no ack) **succeeds** (advisory,
  non-blocking).

## 8. Safe retrieval & consumer filtering (§3.7)

Every read tool (`get_rules`, `search_lessons`, `list_combos`, `get_road`/`list_roads`,
`search`/`list_knowledge`/`search_knowledge`, open `search_thoughts`/`list_thoughts`) gains:

- default `status` filter = **`approved`** (excludes proposed/deprecated/superseded);
- `include_proposed: bool = False` to also return proposed (for a Devon review pass);
- `min_authority: str | None = None` (`informational`/`recommended`/`required`) so standards tools
  consume brain knowledge "only through defined authority rules" (§3.7).

New governance fields (`status`, `authority`, `applicability`, `owner`, `version`, `conflict_note`)
are added to every read tool's result dict, to `code/tools/serialize.py`, and to the REST `/api/*`
builders in each brain's `__init__.py`. Open brain reads behave as today (thoughts are
`approved/informational`, so the approved-default returns them unchanged).

## 9. Testing

Under the existing `ci`/`quality` checks (no new `required_check` declared — nothing new runs outside
CI; per the "a check that runs nowhere is a lie" lesson we do not declare one). New tests
(SQLite-in-memory + the existing fakes pattern):

- mixin present on every governed model; CHECK constraints reject bad `status`/`authority`.
- write tools default `status='proposed'`; `auto_approve` requires the approver key (contributor key
  ignored); `require_approver()` rejects the contributor key and the no-key path.
- approve/reject/deprecate transitions; approve of a flagged record blocked without ack.
- safe-retrieval default (approved only); `include_proposed` and `min_authority` filters.
- conflict detection: the duplicate (Layer 1, ack-gated) and overlap (Layer 2, advisory) tests from
  §7.3, including that the matched approved record is left unmutated and the note is preserved on ack.
- a migration-shape test: apply the governance columns against a throwaway `pgvector:pg16` (see §10)
  and assert backfill counts — run in the pre-merge migration harness, not necessarily in CI.

## 10. Migration execution & verification (orchestrator-run, never delegated)

1. **Pre-merge, per brain:** restore that brain's latest `vps-production` dump into a throwaway
   `pgvector/pgvector:pg16` container; run the brain's new Alembic revision `upgrade head`; assert
   the columns exist, backfill counts are sane (e.g. infra `required` count == the 4 enumerated
   security rules present; `deprecated` count == retired count), MCP smoke (propose→approve→retrieve),
   then `downgrade` cleanly. Mirrors the WS-0.4 restore-drill mechanics.
2. **Merge** only on Devon's explicit signal, after per-task + final reviews are clean.
3. **Post-merge, one brain at a time:** optional `~/Projects/vps-backup/backup.sh` for a zero-RPO
   rollback point → deploy the brain via its existing CI/CD path (image build → Coolify redeploy →
   `start.sh` runs `alembic upgrade head`) via **infraops** (never SSH/UI) → verify live: `/api/health`
   ok, MCP `propose → approve → retrieve honors authority`, and `min_authority` filtering → only then
   the next brain. Order: infra → code → app → open (rules-bearing brains first, where governance
   matters most and the conflict test lives).
4. **Rollback:** additive columns are backward-compatible with the prior image; if a deploy misbehaves,
   redeploy the previous image (columns remain, harmless) and, if truly needed, `downgrade` the
   revision. Restore from the fresh dump is the last resort.

## 11. Exit criteria (D5)

- [ ] Every governed brain record carries `status`/`authority`/provenance/`applicability`/`version`;
      existing records migrated per §6.2 (retired→deprecated, enumerated security→required, rest
      informational; thoughts approved/informational).
- [ ] Agents (contributor key) can only **propose**; **approval is approver-key-only**, enforced in
      `auth.py`/`require_approver()` (structural, not argument-trust).
- [ ] Retrieval defaults to approved and can filter by `authority` (§3.7 consumer path).
- [ ] A same-applicability conflict is **flagged at write** (proposal-only) — demonstrated by the §7.3
      duplicate + overlap tests against a **real migrated `required` rule**; detection is live for infra
      rules at ship (§7.2 honest-scope note).
- [ ] All four brains migrated live and their MCP tools verified post-migration (§10).
- [ ] Phase 1 exit checklist can then be written (identities WS-1.2 + audit WS-1.1 + contract WS-1.3 +
      governed knowledge WS-1.4).

## 12. Files touched (map)

- **new:** `src/core/governance.py` (mixin incl. `conflict_kind`/`conflict_acknowledged_at`,
  `require_approver`, `normalized_check`/`conflict_key`/`find_conflicts`, approve/reject/deprecate tool
  factory), `src/core/__init__` export as needed.
- **config/auth:** `src/core/config.py` (+`contributor_key`), `src/core/auth.py` (accept both keys +
  record tier).
- **per brain (×4):** `models.py` (mixin + supersession cols), one Alembic revision under
  `migrations/versions/`, `tools/*` (propose-only defaults, new approve/reject/deprecate, read-filter
  params, surface new fields), `repositories/*` (status/authority-aware queries), `__init__.py`
  (register governance tools; REST builders). code-brain also `tools/serialize.py`.
- **tests:** `tests/brains/test_*.py` + a new `tests/core/test_governance.py`.
- **docs:** this spec; plan under `docs/superpowers/plans/`; PROJECT.md backlog touch if needed.
