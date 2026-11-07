# Environment roles design

Status: draft, waiting for Devon to read and approve it (2026-11-07). After approval, the next step
is an implementation plan for phases 1, 2, and 4. Phase 3 gets its own plan.

## Intent

When production moved from Hetzner to OVHcloud on 2026-10-23, about 25 approved brain records went
stale. Most of them only needed an IP address or a provider name swapped. They were written against
an address ("178.156.247.239", "the Hetzner VPS") when they meant a role ("the prod VPS").

This design makes the role the thing records and machines refer to, and keeps what each role
currently means in one place. A future host move then changes that one place, plus the short list of
things that need a literal address.

## Goals

- Every brain record and doc can say "the prod VPS" or "dev Coolify" and be correct after a host
  move without being edited.
- One authoritative answer to "what is the prod VPS right now": its provider, addresses, tailnet
  name, and DNS alias. Agents can query it.
- A brain write that names a retired host's address gets caught before it becomes approved guidance.
- Machines (configs, SSH targets, LaunchAgents, DNS) reach the prod host through a name that a move
  repoints, not a literal IP address.

## Non-goals

- Rewriting dated history. "Moved from Hetzner 178.156.247.239 on 2026-10-23" is true as written and
  keeps its literal values.
- Catching records that went stale because *how* something works changed, such as the firewall
  mechanism or VideoCreator moving to the Mac mini. Periodic review covers that; this design doesn't.
- Automating a host move.
- Changing `project-standards/retired-hosts.toml` or `portfolio lint`. They keep guarding tracked
  files.

## Anti-goals

The design fails if any of the following happen, even if it technically works:

- A guard that blocks legitimate history, so sessions learn to work around it or stop writing.
- A second, drifting source of truth: the environment registry and CLAUDE.md's "Infrastructure Quick
  Facts" disagreeing about the prod host.
- DNS work that takes production sites down. Phase 3 is live-DNS work and runs in its own
  infrastructure session.

## Roles

The registry starts with the following roles. The builder can add one if the survey in phase 1 finds
another host that records refer to.

| Role | Means today |
|---|---|
| `prod-vps` | OVHcloud US VPS-3, Vint Hill VA. Public 40.160.102.182, IPv6 2604:2dc0:101:200::5d55, tailnet `ovh-vps1` 100.92.138.19. Runs prod Coolify. |
| `prod-coolify` | Coolify at `http://coolify-1.devonwatkins.com`, on `prod-vps`. |
| `dev-coolify` | Coolify on the OrbStack `ubuntu` machine on the Mac mini (`ssh orb`). |
| `operator-mac` | Devon's Mac mini: LaunchAgents, local tools, the OrbStack host Docker engine. |

## Design

### Phase 1: environment registry in Infra Brain

Add a record type, `environment`, to Infra Brain, with these fields:

- `role`: the stable name, such as `prod-vps`. One current row per role.
- `description`: what the role is for.
- `provider` and `location`.
- `addresses`: public IPv4 and IPv6, tailnet name and IP, and DNS alias.
- `current_since`: the date this host took the role.
- `previous`: a list of `{addresses, provider, until}` for hosts that held the role before. Hetzner
  178.156.247.239 and tailnet 100.112.188.96 go here for `prod-vps`, until 2026-10-23.

Add the following tools:

- `list_environments` and `get_environment(role)`: read tools any key can call.
- `set_environment(role, ...)`: approver key only. When a role's host changes, it moves the current
  addresses into `previous` with today's date.

An environment row is a decision, not a proposal, so writes are approver-only and land approved. The
governance mixin isn't needed.

CLAUDE.md's "Infrastructure Quick Facts" keeps a one-line summary per role, plus a pointer to
`get_environment`. That way, sessions that never query the brain still see the answer.

A host move updates three places: the registry (`set_environment`), the Quick Facts lines, and
`project-standards/retired-hosts.toml`. The workspace CLAUDE.md isn't in a git repository, so the
three can't share one commit. This plan adds a host-move runbook, `docs/operations/host-move.md`,
that lists the three places plus phase 3's literal-address list. An automated check across the
three is a later item, not part of this plan. (My call.)

### Phase 2: write-time guard in every brain

**What matches.** Each `previous` entry carries a list of match strings: its IP addresses, its
tailnet IP address, and its host names (for example `ubuntu-2gb-ash-1`). Provider words like
"Hetzner" never match, because infraops tools and job names use them legitimately. The current
side matches only a role's live IP addresses. Matching is by fixed string, like `retired_hosts.py`.
(My call.)

**Which writes it covers, and what it does.** The guard runs on every path that writes free text
into brain guidance. (My call.)

- **New records:** infra `add_lesson` and `add_rule`, app `capture_knowledge` (including with
  `supersedes_id`), and code `add_lesson`, `add_rule`, `add_exemplar`, and `add_road`.
  - **A retired address:** the record stays proposed, whatever the key and `auto_approve` say. The
    tool response names the address and the role it used to belong to.
  - **A current address:** the record saves as normal. The response carries a `hint` suggesting the
    role name.
- **In-place edits of approved rows:** infra `update_rule`, code `update_road`, and app
  `update_app` (`description` and `environments[].url`). A retired address is refused with an
  error that names it, unless the call passes `allow_retired=True`. Demoting a live row to
  proposed would pull guidance out from under every agent, so these paths refuse instead.
- **Paths that hint only:** the infra REST proposal routes, whose records are already proposed, and
  app `onboard_app`. Onboarding ingests repo docs full of dated history, and holding them would
  flood the queue.

**How a hold is recorded.** A held record gets a new `conflict_kind`, `retired_address`, added to
the CHECK constraint by an Alembic migration in the infra, app, and code brain databases, each
downgrade tested on Postgres. Approving it requires `acknowledge_conflict=True`, the same as a
duplicate, so a person has looked at the flag. If a record is also a duplicate, `conflict_kind`
stays `duplicate`, and `conflict_note` names both. Both need the same acknowledgement. (My call.)

**Where the other brains get the list.** Only Infra Brain holds the registry. It serves the match
strings at an unauthenticated `GET /api/environments/addresses`, which returns role names and
addresses and nothing else. This is the only Infra Brain path that answers without a key, and the
route's tests must show that it returns no other fields. The App Brain and Code Brain deployments
read it and cache it for a few minutes. If Infra Brain is unreachable, the guard skips the check
and logs it; it never fails the write. (Devon's decision, 2026-11-07. Public addresses are already
in DNS, and tailnet addresses can be reached only from inside the tailnet.)

### Phase 3: machine-level names (separate infrastructure session)

This phase is live DNS and host configuration. It gets its own plan, written in that session after
surveying live state, and it isn't part of the plan for phases 1, 2, and 4. It follows infra-brain
standards and goes through infraops. (My call.)

1. Create `prod-vps.devonwatkins.com`, an A record and an AAAA record for `prod-vps`. DNS-only,
   not proxied.
2. Repoint machine references from the literal IP address to the alias, where a name works: SSH
   config and LaunchAgents on `operator-mac` (for example, `com.devon.n8n-oauth-sstunnel`), and
   infraops `VPS_HOST`.
3. Convert app subdomains to CNAMEs of `prod-vps.devonwatkins.com`, where the provider allows it.
   Namecheap allows a CNAME on a subdomain but not on the root of a domain. Cloudflare zones can
   flatten a CNAME at the root. Convert in small batches, and check each site after its change.
4. Rename the tailnet node from `ovh-vps1` to `prod-vps`. Then update tailnet-only callers to use
   the MagicDNS name, such as the VeriTok poller, which uses `100.92.138.19:8010` today. Do this
   only after checking that nothing depends on the old name.
5. Write down the references that must stay literal. That runbook list is what a host move still
   has to change by hand. It includes the Namecheap API allowlist, firewall rules, the alias record
   itself, and root-of-domain A records on Namecheap.

Retiring the Hetzner host fits naturally in the same session. That means removing the server, the
`com.devon.hetzner-snapshot` job, the old allowlist entry, and the offline tailnet node.

### Phase 4: rewrite current-address records to role names (optional)

After the guard exists, one sweep rewrites approved records that name a role's current address, so
they use the role name instead. Dated history stays as it is. The sweep uses the same replace
methods as the 2026-11-07 review: `deprecate` plus a new record for infra lessons, and
`capture_knowledge` with `supersedes_id` for App Brain. Devon confirms the change list before it
runs.

## Constraints

- Brain code follows this repo's CLAUDE.md: query Code Brain for cross-cutting patterns, run
  `make check`, then run `/code-review`.
- A schema change is an Alembic migration in `src/brains/infra/migrations/versions/`. Downgrade gets
  tested against Postgres, not only SQLite (workspace invariant #17).
- The guard must not add a network call to Infra Brain's own writes. Infra Brain reads its registry
  directly.
- A secret never goes in a registry row. Addresses aren't secrets; credentials stay in BWS.

## Decided

- The roles are names, not providers: `prod-vps`, not `ovh-vps`.
- The registry lives in Infra Brain.
- A retired address holds a record for review. A current address only gets a hint.
- Phase 3 runs in its own infrastructure session.
- The address list is served without a key, on one read-only path.

## Left to the builder

- How the registry is stored (table layout, JSON columns) and what the migration looks like.
- The cache lifetime for the address list in the App Brain and Code Brain deployments.
- The exact wording of hints and conflict notes.
- Whether phase 4 runs right after phase 2 or waits for the next review.
