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
applicable_standards: [project, security, code, infra]
coolify_resources: [brain-app, brain-code, brain-infra, brain-open, brain-app-db, brain-code-db, brain-infra-db, brain-open-db, aymdec0jxuaw2slzcs6nhdmp, svbkhx455u1fdvev0k840vse, m10p29dq7aahed7ssi06fnwu, abvedgcsk6a8a2cva5n87jw4, x1rt6fvevdzmkp34a8wprl76, wvnxvjhsblyiosiddnvaghmp, dt6exe82vfmc41cg8sx64q4a, jsvdokywdnwhxxszrq5xavuk]
---

## Backlog
- [ ] (P2) Validate/normalize OpenRouter-extracted metadata before storing: extract_metadata() (src/brains/app/services/openrouter.py) returns json.loads(LLM output) with no schema validation and onboarding.py persists it as app_knowledge.metadata; wrong-shaped valid JSON stores fine but can break downstream search/aggregation. Enforce expected keys/types (pydantic or normalize) before saving; likely also applies to the open brain. Provenance: Codex review 2026-03-24 finding #5, verified still-applicable to unified brain 2026-06-27. — added 2026-06-27
- [ ] (P2) Make the app-brain ← Coolify producer sync write for real: after a couple nights of clean 03:00 dry-run diffs in ~/Library/Logs/infra-drift.log, set APPBRAIN_SYNC_APPLY=1 in ~/.config/infra-drift/env. (PR #28 wired it as a guarded pre-step in drift-audit.sh; runs dry-run only until this flag is flipped.) — added 2026-06-27
- [ ] (P3) Correct app-brain's deployment_url for brain-app from the stale Supabase URL to https://app-brain.devonwatkins.com, so the producer sync can map it (currently brain-app lands in the unmapped list every run). — added 2026-06-27
- [ ] (P3) Decide whether to guard the app-brain producer sync against the dev-Coolify-down env-strip edge case: the merged brain script treats Coolify as authoritative and replaces an app's environments, so if dev Coolify is unreachable at run time AND an app has a dev env recorded, --apply could strip it. Moot today (no app-brain app has a dev env); revisit before/after enabling --apply if any app gains a dev environment. — added 2026-06-27
- [ ] (P2) Wire all four brains (app/infra/open/code) to the BWS-accepted secret-handling pattern instead of plain Coolify env values. Today every brain reads MCP_ACCESS_KEY/POSTGRES_PASSWORD (and OPENROUTER_API_KEY for app/open) directly from Coolify env, and the .bws-secrets.toml UUIDs are all unfilled 0000…0001/0002 placeholders ("filled at cutover", never done). Create the real BWS secrets, fill the manifest UUIDs, wire Coolify->BWS injection (or runtime fetch per build-agent-secrets.md), and rotate the existing values as part of the move. Portfolio-wide: do all four or none for consistency. — added 2026-06-30
- [ ] (P2) Onboard to code-standards (foundation matrix red: code.not-onboarded) — added 2026-07-02
