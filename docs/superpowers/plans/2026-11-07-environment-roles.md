# Environment roles implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Infra Brain an `environment` registry of roles (`prod-vps`, `prod-coolify`, `dev-coolify`,
`operator-mac`), and guard every brain write path against a retired host's address.

**Architecture:** An ungoverned `environments` table in Infra Brain holds one row per role, with its
current addresses and a `previous` list. A pure matcher in `src/core/address_guard.py` turns rows into
an `AddressBook` and scans write text. Infra Brain builds the book from its own database; App Brain and
Code Brain fetch it from the unauthenticated `GET /api/environments/addresses` and cache it.

**Tech stack:** Python 3.12, FastAPI, FastMCP 3, SQLAlchemy 2 async, Alembic, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-environment-roles-design.md` (approved 2026-11-07, PR #124).
Scope is phases 1, 2, and 4, plus the host-move runbook. Phase 3 and retiring the Hetzner server are out
of scope.

## Global constraints

- Work on branch `feat/environment-roles`, never `main`. Ship through one PR.
- Matching is by fixed substring. Provider words such as "Hetzner" never match.
- Current-side match strings: `ipv4`, `ipv6`, `tailnet_ip`. Retired-side match strings: those three plus
  `tailnet_node` and every entry in `hostnames`.
- A retired hit holds a new record as `proposed` whatever the key and `auto_approve` say.
- `GET /api/environments/addresses` is the only Infra Brain path that answers without a key, and it
  returns role names and match strings only.
- The guard never adds a network call to Infra Brain's own writes, and App or Code Brain never fails a
  write because Infra Brain is unreachable. They log a warning and skip.
- No DNS names in any registry row. No secrets in any registry row.
- Every schema change is an Alembic migration whose downgrade is tested on Postgres (invariant #17).
- Logging uses the module's stdlib `logging.getLogger(...)`, which matches the repo. Never swallow an
  exception silently (Code Brain BLOCK rule 4).
- The HTTP fetch sends `User-Agent: brain-address-guard/1 (<brain_type>-brain)`. Cloudflare returns 403
  to default Python user agents on `devonwatkins.com` (invariant #11).
- `make check` is green before every commit.

## Decisions this plan makes where the spec and the code disagree

Devon approves these with the plan.

1. **`add_road` refuses instead of holding.** The spec lists `add_road` with the new records that get
   held as proposed. `Road` has no governance columns, so it can't be held. `add_road` gets the edit-path
   treatment: a retired address is refused unless the call passes `allow_retired=True`.
2. **`set_environment` takes an explicit `previous`.** Seeding needs Hetzner's real `until` date
   (2026-10-23), not today's. When `previous` is passed, it replaces the stored list. When it's omitted
   and any current-side address changed, the old addresses rotate into `previous` with today's date. The
   same addresses mean no rotation.
3. **Open Brain's model and database drift by one CHECK value.** `governance_check_constraints` is
   shared, so Open Brain's model gains `retired_address` in its CHECK, but Open Brain gets no migration
   (it has no guarded write path and never writes the value). This is accepted drift.
4. **A current address wins over a retired one.** If an address is current for any role, it isn't
   treated as retired, so a reassigned address doesn't hold live guidance.
5. **Code Brain's REST proposal routes also hint.** The spec names only the Infra Brain routes. Code
   Brain's `POST /api/proposals/*` routes write the same kind of free text, so they get the same hint.
6. **An overlap flag yields to `retired_address`.** Overlap is advisory, so `conflict_kind` becomes
   `retired_address` and `conflict_note` keeps both notes. A duplicate stays `duplicate`, as the spec says.

## Review focus

1. **A held record that the approver tries to approve without acknowledgement.** `approve` must return
   `conflict_unacknowledged` for `retired_address`, the same as for a duplicate. Test in Task 3.
2. **App or Code Brain when Infra Brain returns 403, times out, or returns bad JSON.** The write succeeds,
   a warning is logged, and the cache doesn't store the failure for the full TTL. Test in Task 5.
3. **A downgrade with held rows present.** The downgrade must clear `retired_address` before it restores
   the narrower CHECK, or it fails on Postgres. Test in Task 3 on Postgres.
4. **The unauthenticated route leaking fields.** It returns only `role`, `current`, and `retired`, and
   every other path still returns 401 without a key. Test in Task 4.
5. **`set_environment` called twice with the same addresses.** No duplicate `previous` entry. Test in
   Task 1.

---

### Task 1: Environment registry in Infra Brain

**Files:**
- Create: `src/brains/infra/migrations/versions/0005_environments.py`
- Create: `src/brains/infra/repositories/environments.py`
- Create: `src/brains/infra/tools/environments.py`
- Modify: `src/brains/infra/models.py` (add `Environment`)
- Modify: `src/brains/infra/__init__.py` (`register` calls `register_environment_tools`)
- Test: `tests/brains/test_infra_environments.py`; `tests/brains/test_infra.py` (`EXPECTED_TOOLS`)

**Interfaces:**
- Produces: `Environment` model with columns `id`, `role` (Text, unique), `description`, `provider`,
  `location` (Text), `addresses` (JSON, `{ipv4, ipv6, tailnet_node, tailnet_ip, hostnames: [str]}`),
  `current_since` (Date), `previous` (JSON list of `{addresses, provider, until}`, default `[]`),
  `updated_at`, `updated_by`. Use `sa.JSON().with_variant(JSONB, "postgresql")` so SQLite tests bind.
- Produces: `EnvironmentRepository(session)` with `list_all() -> list[Environment]`,
  `get(role: str) -> Environment | None`, `upsert(role, fields: dict, previous: list[dict] | None,
  today: date) -> tuple[Environment, bool]` (the bool says whether it rotated).
- Produces: MCP tools `list_environments() -> {"environments": [dict]}`,
  `get_environment(role: str) -> dict` (`{"error": "not_found", "role": ...}` when absent), and
  `set_environment(role: str, description: str, provider: str, location: str, addresses: dict,
  current_since: str, previous: list[dict] | None = None, updated_by: str = "devon") -> dict`.
- Produces: `environment_dict(env: Environment) -> dict` in the tools module.

- [ ] **Step 1: Create the branch.** Run `git switch -c feat/environment-roles`.
- [ ] **Step 2: Write failing tests** in `tests/brains/test_infra_environments.py` against an in-memory
  SQLite engine, following the engine and session patching in `tests/brains/test_infra.py`:
  - `test_set_environment_requires_approver`: contributor caller gets `{"error": "not_authorized"}`.
  - `test_set_environment_creates_row`: approver creates `prod-vps`; `get_environment("prod-vps")`
    returns the `addresses` and `previous` passed in.
  - `test_set_environment_rotates_on_address_change`: a second call with `ipv4` changed and no
    `previous` appends `{addresses: <old>, provider: <old>, until: <today ISO>}` and returns
    `rotated: True`.
  - `test_set_environment_same_addresses_no_rotation`: a repeat call leaves `previous` unchanged and
    returns `rotated: False`.
  - `test_set_environment_explicit_previous_replaces`: passing `previous` stores exactly that list.
  - `test_set_environment_rejects_unknown_address_keys`: `addresses={"dns": "x"}` returns
    `{"error": "invalid_addresses", "allowed": [...]}`.
  - `test_set_environment_rejects_bad_dates`: `current_since="soon"` and a `previous` entry with a bad
    `until` return `{"error": "invalid_date", ...}`.
  - `test_get_environment_not_found` and `test_list_environments_any_key`.
  - In `tests/brains/test_infra.py`, add the three tool names to `EXPECTED_TOOLS`.
- [ ] **Step 3: Run** `pytest tests/brains/test_infra_environments.py tests/brains/test_infra.py -q`.
  Expected: FAIL, tools not registered.
- [ ] **Step 4: Implement** the model, repository, tools, and registration. Only the address fields in
  the `Environment` docstring are allowed. Rotation compares the current-side match strings only (a
  hostname edit alone doesn't rotate). Environments aren't governed: don't add them to
  `register_governance_tools`.
- [ ] **Step 5: Write migration 0005** (`revises 0004`): create `environments` with a unique index on
  `role`, `previous` server default `'[]'::jsonb`, `addresses` NOT NULL. Downgrade drops the table.
- [ ] **Step 6: Run** the Step 3 command and `make check`. Expected: PASS.
- [ ] **Step 7: Commit** `feat(infra): environment registry with list/get/set_environment`.

### Task 2: Address matcher in core

**Files:**
- Create: `src/core/address_guard.py`
- Test: `tests/core/test_address_guard.py`

**Interfaces:**
- Produces: `AddressBook` (frozen dataclass): `current: dict[str, str]` and `retired: dict[str, str]`,
  each mapping a match string to its role.
- Produces: `AddressHits` (frozen dataclass): `retired: tuple[tuple[str, str], ...]`,
  `current: tuple[tuple[str, str], ...]` of `(match, role)`; property `any_retired: bool`.
- Produces: `book_from_payload(payload: dict) -> AddressBook`, where `payload` has the route's shape
  `{"roles": [{"role", "current": [str], "retired": [str]}]}`. Applies decision 4.
- Produces: `payload_from_environments(rows: Iterable) -> dict`, the route's payload, built from
  `Environment` rows using the match-string rules in the global constraints.
- Produces: `scan(texts: Iterable[str | None], book: AddressBook | None) -> AddressHits`. A `None` book
  returns empty hits.
- Produces: `hold_for_retired(data: dict, hits: AddressHits) -> None`. It runs after
  `finalize_governance` on a new record's insert dict.
- Produces: `guard_fields(hits: AddressHits) -> dict` to merge into a tool response:
  `{"retired_address": [{"match", "role"}], "note": ...}` for retired hits, and
  `{"hint": "<match> is the current address of <role>; say '<role>' so the record survives a host move"}`
  for current hits. Empty dict when there are no hits.
- Produces: `refusal(hits: AddressHits) -> dict`:
  `{"error": "retired_address", "matches": [{"match", "role"}], "hint": "pass allow_retired=True for dated history"}`.
- Produces: `retired_note(hits: AddressHits) -> str`, for example
  `"names 178.156.247.239, a retired address of prod-vps"`.

- [ ] **Step 1: Write failing tests**, with a book built from a `prod-vps` payload whose retired list
  holds `178.156.247.239`, `100.112.188.96`, and `ubuntu-2gb-ash-1`, and whose current list holds
  `40.160.102.182`:
  - `test_scan_retired_ip`, `test_scan_retired_hostname`, `test_scan_current_ip_is_hint_only`.
  - `test_provider_word_never_matches`: "the Hetzner box" gives no hits.
  - `test_scan_none_book_and_none_text`: no hits, no exception.
  - `test_current_wins_over_retired`: an address in both lists counts as current only.
  - `test_hold_for_retired_demotes_approved`: an approved insert dict becomes `proposed`, loses
    `reviewed_by` and `reviewed_at`, and gets `conflict_kind == "retired_address"`.
  - `test_hold_keeps_duplicate_kind`: with `conflict_kind == "duplicate"`, the kind stays `duplicate`
    and `conflict_note` contains both the duplicate note and the retired note.
  - `test_hold_replaces_overlap_kind`: overlap becomes `retired_address`; the note keeps both.
  - `test_payload_from_environments_shape`: keys are exactly `role`, `current`, `retired`; hostnames
    appear only on the retired side.
- [ ] **Step 2: Run** `pytest tests/core/test_address_guard.py -q`. Expected: FAIL, module missing.
- [ ] **Step 3: Implement** the module. It imports nothing from any brain package.
- [ ] **Step 4: Run** the Step 2 command and `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): address matcher for the retired-address guard`.

### Task 3: `retired_address` conflict kind, approval, and migrations

**Files:**
- Modify: `src/core/governance.py` (`CONFLICT_RETIRED_ADDRESS`, CHECK tuple, `_approve_record`)
- Create: `src/brains/infra/migrations/versions/0006_retired_address_conflict.py` (`rules`, `lessons`,
  `combos`)
- Create: `src/brains/app/migrations/versions/0008_retired_address_conflict.py` (`app_knowledge`)
- Create: `src/brains/code/migrations/versions/0003_retired_address_conflict.py` (`rules`, `lessons`,
  `exemplars`)
- Test: `tests/core/test_governance.py`

**Interfaces:**
- Produces: `CONFLICT_RETIRED_ADDRESS = "retired_address"` in `src/core/governance.py`, used by Task 2's
  `hold_for_retired`.

- [ ] **Step 1: Write failing tests** in `tests/core/test_governance.py`:
  - `test_approve_retired_address_requires_ack`: returns `conflict_unacknowledged` without
    `acknowledge_conflict`, and approves with it, setting `conflict_acknowledged_at`.
  - `test_check_constraints_allow_retired_address`: the generated CHECK text contains `'retired_address'`.
- [ ] **Step 2: Run** `pytest tests/core/test_governance.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `_approve_record` treats both kinds in
  `(CONFLICT_DUPLICATE, CONFLICT_RETIRED_ADDRESS)` as needing acknowledgement. Update the `approve` tool
  docstring to say so.
- [ ] **Step 4: Write the three migrations.** Each upgrade drops and re-creates
  `ck_<table>_conflict_kind` with `('duplicate', 'overlap', 'retired_address')`. Each downgrade first runs
  `UPDATE <table> SET conflict_kind = NULL WHERE conflict_kind = 'retired_address'` (the rows stay
  proposed and keep their note), then restores the two-value CHECK.
- [ ] **Step 5: Test each downgrade on Postgres.** Start a throwaway database:
  `docker run --rm -d --name brain-mig-pg -e POSTGRES_PASSWORD=pg -p 55432:5432 pgvector/pgvector:pg16`.
  For each of `infra`, `app`, and `code`, in its own database (`createdb` inside the container):
  1. `DATABASE_URL=postgresql+asyncpg://postgres:pg@localhost:55432/<b> alembic -c src/brains/<b>/alembic.ini upgrade head`
  2. Insert one governed row with `conflict_kind = 'retired_address'`.
  3. `alembic ... downgrade -1`. Expected: succeeds; the row has `conflict_kind IS NULL`.
  4. `alembic ... upgrade head`. Expected: succeeds.
  Paste the command output into the PR description. Stop the container afterward.
- [ ] **Step 6: Run** `make check`. Expected: PASS.
- [ ] **Step 7: Commit** `feat(governance): retired_address conflict kind with migrations`.

### Task 4: Guard in Infra Brain, and the unauthenticated address route

**Files:**
- Modify: `src/brains/infra/repositories/environments.py` (`address_book()`)
- Modify: `src/brains/infra/tools/lessons.py`, `src/brains/infra/tools/rules.py`,
  `src/brains/infra/services/proposals.py`, `src/brains/infra/__init__.py`
- Test: `tests/brains/test_infra_guard.py`; `tests/brains/test_infra.py` (capabilities)

**Interfaces:**
- Consumes: Task 2's `scan`, `hold_for_retired`, `guard_fields`, `refusal`, `payload_from_environments`,
  `book_from_payload`.
- Produces: `EnvironmentRepository.address_book() -> AddressBook`, read in the write's own session.
- Produces: `propose_lesson` and `propose_rule` return `(record, error, guard: dict)`; the REST routes
  merge `guard` into the 201 body.
- Produces: `capabilities.auth_exact == ("/api/health", "/api/environments/addresses")`.

Scanned fields: `add_lesson` scans `title` and `content`. `add_rule` scans `rule`, `reason`, and
`json.dumps(check)`. `update_rule` scans the fields it's given (`reason`, `source_app`, and
`json.dumps(check)`).

- [ ] **Step 1: Write failing tests.** Use fake repositories for `add_lesson`, like
  `test_add_lesson_approver_default_holds_only_critical`, and patch `address_book` to a fixed book.
  - `test_add_lesson_retired_held_even_with_approver_and_auto_approve`: approver key plus
    `auto_approve=True` still gives `status == "proposed"`, `conflict_kind == "retired_address"`, and a
    response that names `178.156.247.239` and `prod-vps`.
  - `test_add_lesson_current_address_saves_with_hint`: status `approved`; response has `hint`.
  - `test_add_rule_retired_in_check_json_held`.
  - `test_update_rule_retired_refused` and `test_update_rule_allow_retired_applies`.
  - `test_rest_proposal_hint_only`: a `POST /api/proposals/lessons` with a retired address stays
    proposed with no `conflict_kind` set, and the body carries `retired_address`.
  - `test_addresses_route_unauthenticated`: no key, 200, body equals
    `payload_from_environments(rows)`, and each role has only `role`, `current`, and `retired`.
  - `test_other_routes_still_need_key`: `GET /api/rules` without a key returns 401.
  - Update `test_infra_capabilities` to the new `auth_exact` tuple.
- [ ] **Step 2: Run** `pytest tests/brains/test_infra_guard.py tests/brains/test_infra.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement.** `update_rule` gains `allow_retired: bool = False`. The route is
  `@app.get("/api/environments/addresses")`.
- [ ] **Step 4: Run** the Step 2 command and `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(infra): retired-address guard and public address route`.

### Task 5: Cached address source for App Brain and Code Brain

**Files:**
- Modify: `src/core/address_guard.py` (`fetch_address_book`)
- Modify: `src/core/config.py` (`infra_brain_url`)
- Modify: `tests/conftest.py` (add `INFRA_BRAIN_URL` to `_BRAIN_ENV_VARS`)
- Test: `tests/core/test_address_guard_fetch.py`

**Interfaces:**
- Produces: `Settings.infra_brain_url: str = "https://infra-brain.devonwatkins.com"`.
- Produces: `async fetch_address_book() -> AddressBook | None`. It GETs
  `{infra_brain_url}/api/environments/addresses` with httpx, a 5-second timeout, and the global
  User-Agent. A success is cached for `CACHE_TTL_S = 300`. A failure logs
  `logger.warning("address guard skipped: %s", exc)` and is cached for `FAILURE_TTL_S = 30`, so an outage
  doesn't add a 5-second timeout to every write.
- Produces: `reset_address_cache() -> None`, for tests.

- [ ] **Step 1: Write failing tests** with `httpx.MockTransport` (or `respx` if it's already in
  `requirements-dev.txt`):
  - `test_fetch_caches_success`: two calls within the TTL make one request.
  - `test_fetch_sends_identifying_user_agent`: the request's `User-Agent` starts with
    `brain-address-guard/`.
  - `test_fetch_403_logs_and_returns_none` (caplog has the warning), `test_fetch_timeout_returns_none`,
    `test_fetch_bad_json_returns_none`.
  - `test_failure_cached_briefly`: a second call within `FAILURE_TTL_S` makes no request; after it, one.
- [ ] **Step 2: Run** `pytest tests/core/test_address_guard_fetch.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Use `time.monotonic()` for the TTL, and pass a clock in tests.
- [ ] **Step 4: Run** the Step 2 command and `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): cached Infra Brain address source for the other brains`.

### Task 6: Guard in App Brain

**Files:**
- Modify: `src/brains/app/tools/knowledge.py` (`capture_knowledge`, `onboard_app`)
- Modify: `src/brains/app/tools/apps.py` (`update_app`)
- Test: `tests/brains/test_app_guard.py`

**Interfaces:**
- Consumes: Task 5's `fetch_address_book`, Task 2's helpers.

Scanned fields: `capture_knowledge` scans `content`, with or without `supersedes_id`. `update_app` scans
`description` and each `environments[].url`. `onboard_app` scans the provided blobs and `description`
before it schedules the background job.

- [ ] **Step 1: Write failing tests**, patching `fetch_address_book` to a fixed book:
  - `test_capture_retired_held_with_approver_and_auto_approve`, including the `supersedes_id` case.
    The held chunk is `proposed` with `conflict_kind == "retired_address"`. The superseded chunk stays
    approved: a held replacement must not retire live guidance. When the new chunk is held, store its
    `supersedes_id` but skip `knowledge_repo.supersede`, and say in the response that the reviewer
    deprecates the old chunk on approval. `approve` doesn't process the link.
  - `test_capture_current_address_hint`.
  - `test_capture_infra_unreachable_saves_normally`: book `None`, normal behavior.
  - `test_update_app_retired_in_env_url_refused` and `test_update_app_allow_retired_applies`.
  - `test_onboard_app_retired_hint_only`: the response has `retired_address`, and onboarding is still
    scheduled.
- [ ] **Step 2: Run** `pytest tests/brains/test_app_guard.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `update_app` gains `allow_retired: bool = False`. Fetch the book before
  opening the session, so the HTTP call isn't held inside a database transaction.
- [ ] **Step 4: Run** the Step 2 command and `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(app): retired-address guard on App Brain writes`.

### Task 7: Guard in Code Brain

**Files:**
- Modify: `src/brains/code/tools/lessons.py`, `src/brains/code/tools/rules.py`,
  `src/brains/code/tools/exemplars.py`, `src/brains/code/tools/roads.py`,
  `src/brains/code/services/proposals.py`, `src/brains/code/__init__.py`
- Test: `tests/brains/test_code_guard.py`

**Interfaces:**
- Consumes: Task 5's `fetch_address_book`, Task 2's helpers.
- Produces: Code Brain `propose_lesson` and `propose_rule` return `(record, error, guard: dict)`, the
  same as Infra Brain's.

Scanned fields: `add_lesson` scans `title` and `content`. `add_rule` scans `rule`, `reason`,
`good_example`, `bad_example`, and `json.dumps(check)`. `add_exemplar` scans `label`, `location`, and
`note`. `add_road` and `update_road` scan every string field they're given.

- [ ] **Step 1: Write failing tests.** Use fake repositories for `add_lesson` (`tags` is an ARRAY).
  - `test_code_add_lesson_retired_held_with_approver_and_auto_approve`.
  - `test_code_add_rule_retired_held`, `test_code_add_exemplar_retired_held`.
  - `test_add_road_retired_refused`, `test_add_road_allow_retired_creates` (decision 1).
  - `test_update_road_retired_refused`, `test_update_road_allow_retired_applies`.
  - `test_code_rest_proposal_hint_only` (decision 5).
- [ ] **Step 2: Run** `pytest tests/brains/test_code_guard.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `add_road` and `update_road` gain `allow_retired: bool = False`, threaded
  through `_do_add_road` and `_do_update_road`.
- [ ] **Step 4: Run** the Step 2 command and `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(code): retired-address guard on Code Brain writes`.

### Task 8: Host-move runbook and docs

**Files:**
- Create: `docs/operations/host-move.md`
- Modify: `CLAUDE.md` (one line pointing to the runbook)
- Modify: `PROJECT.md` (the backlog item's `Plan:` reference, already added with this plan)

- [ ] **Step 1: Write the runbook.** A numbered procedure for moving a role to a new host:
  1. Call `set_environment` for the role with the approver key. The old addresses rotate into
     `previous`; check the response's `rotated: true`.
  2. Edit the role's line in `/Users/devon/Projects/CLAUDE.md` "Infrastructure Quick Facts".
  3. Add the retired addresses to `project-standards/retired-hosts.toml` in the same change as the move.
  4. Verify: `curl -s https://infra-brain.devonwatkins.com/api/environments/addresses` lists the old
     addresses under `retired`. Wait five minutes for the App and Code Brain caches, then probe one write
     per brain and reject the probes.
  Include a section that says what stays literal: dated history, and the references phase 3 lists.
- [ ] **Step 2: Run** `make check`. Expected: PASS.
- [ ] **Step 3: Commit** `docs: host-move runbook`.

### Task 9: Review, ship, seed, and verify live

- [ ] **Step 1: Review.** Run `/code-review` on the branch diff and fix its findings. Then dispatch a
  whole-branch review on Opus.
- [ ] **Step 2: Open the PR** with the Postgres migration output from Task 3. After CI passes, merge it.
- [ ] **Step 3: Watch CI** until the deploy job reports every brain serving the merge commit's `GIT_SHA`.
- [ ] **Step 4: Seed the registry.** Run a scratch script (in the session scratchpad, not the repo) that
  reads the Infra Brain approver key from BWS (`bws secret get <uuid> --color no`, Keychain token at
  service `Claude`, account `BWS_ACCESS_TOKEN_BRAIN_OPS`), never prints it, and calls `set_environment`
  over `/mcp` for each role:
  - `prod-vps`: provider `OVHcloud`, location `Vint Hill, VA, US (VPS-3)`, addresses
    `{ipv4: 40.160.102.182, ipv6: 2604:2dc0:101:200::5d55, tailnet_node: ovh-vps1, tailnet_ip: 100.92.138.19}`,
    `current_since: 2026-10-23`, `previous: [{provider: Hetzner, until: 2026-10-23, addresses:
    {ipv4: 178.156.247.239, tailnet_ip: 100.112.188.96, hostnames: [ubuntu-2gb-ash-1]}}]`.
  - `prod-coolify`: the production Coolify instance, on `prod-vps`; no addresses of its own.
  - `dev-coolify`: Coolify on the OrbStack `ubuntu` machine on the Mac mini.
  - `operator-mac`: Devon's Mac mini: LaunchAgents, local tools, and the OrbStack host Docker engine.
  `current_since` for the last three: the earliest date Devon confirms, or the seeding date if unknown.
- [ ] **Step 5: Check the public route.** `curl -s https://infra-brain.devonwatkins.com/api/environments/addresses`
  lists `178.156.247.239` under `prod-vps`'s `retired`.
- [ ] **Step 6: Probe through the chat connectors.** `add_lesson` already exists in the connector tool
  list, so this session can use it. Through the Infra Brain connector, add a lesson whose content names
  `178.156.247.239`. Expected: `proposed`, with the `retired_address` flag. After the five-minute cache,
  repeat through the Code Brain connector's `add_lesson`; this probes the HTTP path. Reject both probes
  with `reject`.
- [ ] **Step 7: Update** the Quick Facts in `/Users/devon/Projects/CLAUDE.md`: one line per role plus
  "Query `get_environment(role)` in Infra Brain for the current answer."

### Task 10: Phase 4 sweep (only after Devon confirms the change list)

- [ ] **Step 1: List candidates.** Search approved records in all three brains for each current-side
  match string (`40.160.102.182`, `2604:2dc0:101:200::5d55`, `100.92.138.19`). Use `search_lessons`,
  `get_rules`, Code Brain `search`, and App Brain `search_knowledge` in keyword mode.
- [ ] **Step 2: Draft the change list** in the session scratchpad: record id, the current text, and the
  proposed text with the role name. Leave dated history as it is.
- [ ] **Step 3: Stop and get Devon's confirmation** on the list.
- [ ] **Step 4: Apply.** Infra lessons: `deprecate` plus a new `add_lesson`. Infra rules: `update_rule`.
  App Brain: `capture_knowledge` with `supersedes_id`. Code Brain: the matching replace method. Report
  each new id.
