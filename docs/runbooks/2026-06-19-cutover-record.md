# Brain Unification Cutover — Historical Record

Preserved verbatim from `/root/brain-cutover/cutover-log.txt` (prod VPS) on 2026-07-02,
immediately before the cutover rollback artifacts were decommissioned (WS-0.1: the three
stopped old apps had already been deleted from Coolify, the old repos archived; the
orphaned `*_postgres-data` volumes and `/root/brain-cutover/` dumps were removed after
the 2026-07-02 restoration exercise proved the nightly backups restore correctly).

```
infra canary cutover complete 2026-06-19T19:51:51Z: infra-brain.devonwatkins.com -> brain-infra (m10p29dq7aahed7ssi06fnwu) on managed pgvector DB dt6exe82vfmc41cg8sx64q4a; old app hg8kkgo0kwoo8goswswgsko0 STOPPED (intact for rollback); pg_dump at /root/brain-cutover/infrabrain-20260619.sql
=== BRAIN UNIFICATION CUTOVER COMPLETE 2026-06-19T20:29:21Z ===
infra: brain-infra m10p29dq7aahed7ssi06fnwu -> managed db dt6exe82vfmc41cg8sx64q4a (40r/65l/4c/29v) | old hg8kkgo0kwoo8goswswgsko0 STOPPED
open:  brain-open  abvedgcsk6a8a2cva5n87jw4 -> managed db jsvdokywdnwhxxszrq5xavuk (59 thoughts)     | old e0000okgowcgkw0wosgo8kg8 STOPPED (domain moved to placeholder)
app:   brain-app   aymdec0jxuaw2slzcs6nhdmp -> managed db x1rt6fvevdzmkp34a8wprl76 (22 apps/264 know) | old x8gkog0ow8k4oo80occ08g0w STOPPED
snapshots in /root/brain-cutover/{infrabrain,openbrain,appbrain}-20260619.sql
ROLLBACK per brain: restart old app + move FQDN back. Old volumes intact: *_postgres-data
```

(code-brain was added later — deployed 2026-06-30 as brain-code `svbkhx455u1fdvev0k840vse`
with managed DB brain-code-db `wvnxvjhsblyiosiddnvaghmp`; it was never part of the cutover.)
