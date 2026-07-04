---
name: brain
tier: active
status: active
purpose: Unified knowledge platform serving app-brain, infra-brain, open-brain, and
  code-brain (the portfolio-wide code-pattern registry) behind one MCP/API.
version: n/a
version_source: none
updated: '2026-06-30'
foundation: true
foundation_contract: 1
applicable_standards:
  project: '1.0'
  security: '1.0'
  code: '1.0'
  infra: null
required_checks:
- id: quality
  executor: github-actions:quality.yml
- id: ci
  executor: github-actions:ci.yml
coolify_resources: [brain-app, brain-code, brain-infra, brain-open, brain-app-db, brain-code-db, brain-infra-db, brain-open-db, aymdec0jxuaw2slzcs6nhdmp, svbkhx455u1fdvev0k840vse, m10p29dq7aahed7ssi06fnwu, abvedgcsk6a8a2cva5n87jw4, x1rt6fvevdzmkp34a8wprl76, wvnxvjhsblyiosiddnvaghmp, dt6exe82vfmc41cg8sx64q4a, jsvdokywdnwhxxszrq5xavuk]
---

## Backlog

- [x] (P1) BEFORE issuing the contributor MCP key to any real agent: gate the destructive/de-escalation governance tools behind require_approver — delete_rule/retire_rule/delete_knowledge, capture_knowledge(supersedes_id=…), and update_rule of an authority='required' record. Today these are contributor-reachable (accepted risk, audit-log-compensated, contributor key unset). Ref spec §5.6. Provenance: WS-1.4 Fable final review #5. — added 2026-07-03. **CLOSED 2026-07-04: PR #19 merged, Fable adversarial review confirmed all five gates sound (no bypass), CI green, all four brains (infra/open/app/code) redeployed and healthy post-merge.**
- [ ] (P1) `onboard_app` (src/brains/app/tools/knowledge.py) is an ungated self-approve + de-escalation surface: it hardcodes new chunks to `status=approved` in the background job (src/brains/app/services/onboarding.py) with no require_approver() check at all, and `replace_existing=True` deactivates existing approved onboard-sourced chunks — a contributor-tier key can both self-approve new governed knowledge and de-escalate existing approved knowledge via this one tool, without ever presenting the approver key. Same bug class as the restore_rule bypass WS-1.4 caught. Not covered by the WS-2.4 five-call pilot (out of its stated scope). Provenance: Fable adversarial review of WS-2.4's approver-gate PR, 2026-07-04. — added 2026-07-04
- [ ] (P3) Surface owner/version/conflict_note in the governance read-tool result dicts (spec §8 literal text lists them; currently only status/authority/applicability/conflict are surfaced, uniformly across infra/code/app) — or amend §8 to match. Provenance: WS-1.4 final review. — added 2026-07-03
- [ ] (P3) app search_knowledge semantic path governance-filters via a Python post-filter over pgvector results (3× over-fetch); can under-return `limit` when >2/3 of ranked candidates are non-approved. No leak. Honest fix = governance-aware plpgsql match function. Provenance: WS-1.4 final review. — added 2026-07-03
- [ ] (P3) Add a state-machine guard to approve/reject/deprecate (e.g. approve only from proposed) to prevent fat-finger resurrection of deprecated/superseded records; get_combo (infra) has no status filter / omits governance fields (combos seed-only, latent); app migration doesn't backfill historical supersession (old inactive chunks land deprecated w/ superseded_by_id NULL). Provenance: WS-1.4 final review minors. — added 2026-07-03
- [ ] (P2) Rotate brain secret values (MCP keys re-key the claude.ai connectors; PG passwords need the safe create→deploy→verify→revoke lane) once the WS-0.7 rotation class exists — added 2026-07-02
- [ ] (P2) Validate/normalize OpenRouter-extracted metadata before storing: extract_metadata() (src/brains/app/services/openrouter.py) returns json.loads(LLM output) with no schema validation and onboarding.py persists it as app_knowledge.metadata; wrong-shaped valid JSON stores fine but can break downstream search/aggregation. Enforce expected keys/types (pydantic or normalize) before saving; likely also applies to the open brain. Provenance: Codex review 2026-03-24 finding #5, verified still-applicable to unified brain 2026-06-27. — added 2026-06-27
- [ ] (P2) Make the app-brain ← Coolify producer sync write for real: after a couple nights of clean 03:00 dry-run diffs in ~/Library/Logs/infra-drift.log, set APPBRAIN_SYNC_APPLY=1 in ~/.config/infra-drift/env. (PR #28 wired it as a guarded pre-step in drift-audit.sh; runs dry-run only until this flag is flipped.) — added 2026-06-27
- [ ] (P3) Correct app-brain's deployment_url for brain-app from the stale Supabase URL to https://app-brain.devonwatkins.com, so the producer sync can map it (currently brain-app lands in the unmapped list every run). — added 2026-06-27
- [ ] (P3) Decide whether to guard the app-brain producer sync against the dev-Coolify-down env-strip edge case: the merged brain script treats Coolify as authoritative and replaces an app's environments, so if dev Coolify is unreachable at run time AND an app has a dev env recorded, --apply could strip it. Moot today (no app-brain app has a dev env); revisit before/after enabling --apply if any app gains a dev environment. — added 2026-06-27
- [x] (P2) Wire all four brains (app/infra/open/code) to the BWS-accepted secret-handling pattern instead of plain Coolify env values. Today every brain reads MCP_ACCESS_KEY/POSTGRES_PASSWORD (and OPENROUTER_API_KEY for app/open) directly from Coolify env, and the .bws-secrets.toml UUIDs are all unfilled 0000…0001/0002 placeholders ("filled at cutover", never done). Create the real BWS secrets, fill the manifest UUIDs, wire Coolify->BWS injection (or runtime fetch per build-agent-secrets.md), and rotate the existing values as part of the move. Portfolio-wide: do all four or none for consistency. — added 2026-06-30
- [x] (P2) Onboard to code-standards (foundation matrix red: code.not-onboarded) — added 2026-07-02
