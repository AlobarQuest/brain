# Move a role to a new host

This runbook moves an environment role, such as `prod-vps`, from one host to another. A host move changes addresses in three places, and the three can't share one commit, so this page is the checklist that keeps them in step.

The three places are:

- The Infra Brain environment registry, which the App Brain and Code Brain read to guard their writes.
- The role's line in `/Users/devon/Projects/CLAUDE.md`, under "Infrastructure Quick Facts". This file isn't in any git repository.
- `~/Projects/project-standards/retired-hosts.toml`, which `portfolio lint` uses to warn on tracked files that still name a retired address.

An automated check across the three places is a later backlog item. Until it exists, finish every step below.

## Before you begin

You need:

- The approver key for Infra Brain. Fetch it from Bitwarden Secrets Manager (BWS) at call time. The BWS token is in the Keychain at service `Claude`, account `BWS_ACCESS_TOKEN_BRAIN_OPS`, and the secret UUIDs are in `.bws-secrets.toml`. Pass `--color no` to every `bws` call that you parse. Don't print the key or paste it into a prompt.
- The new host's addresses: `ipv4`, and where they exist, `ipv6`, `tailnet_node`, `tailnet_ip`, and `hostnames`. Use addresses only. The registry doesn't accept DNS names in the address fields.

  `ipv4` and `tailnet_ip` are each one IPv4 address, `ipv6` is one IPv6 address, and `tailnet_node` is one non-empty string. `hostnames` is a list of strings, even when there's only one, for example `["ubuntu-2gb-ash-1"]`. The guard matches every value as a whole address, ignoring case: a retired `100.92.138.1` doesn't match inside `100.92.138.19`, but it does match `100.92.138.1:22`. Even so, `tailnet_node` and each hostname must be at least 4 characters long and contain a digit, `.`, `:`, or `-`. A plain name such as `ubuntu` is refused, because it would hold most records in every brain.
- The role name. Run `list_environments` on Infra Brain to see the roles.

## Move the role

1. Read the role's current record with `get_environment(role)`. Note the current addresses, because they become the retired addresses.

1. Call `set_environment` with the approver key. Pass the role, a description, the provider, the location, the new `addresses`, and `current_since` set to the move date in ISO format.

   Leave `previous` out. When a current-side address (`ipv4`, `ipv6`, `tailnet_ip`) that had a value changes or is removed and you omit `previous`, the registry rotates the old addresses into `previous` and stamps them with today's date. Adding a current-side address the role didn't have isn't a move and doesn't rotate.

1. Check that the response contains `rotated: true`. If it doesn't, no current-side address that had a value changed or was removed. Compare your input with the record from step 1.

1. Open `/Users/devon/Projects/CLAUDE.md` and edit the role's line under "Infrastructure Quick Facts" so that it names the new host. Keep the dated history on that line, for example the "Moved from ... on YYYY-MM-DD" sentence.

1. Open `~/Projects/project-standards/retired-hosts.toml` and add one `[[host]]` entry for each retired address. Each entry needs four fields:

   - `address`: the IP address or hostname, matched as a fixed string.
   - `label`: what the address was.
   - `replacement`: where readers go now.
   - `retired`: the retirement date in `YYYY-MM-DD` format.

   Make this edit in the same change as the move, so `portfolio lint` starts warning at once about files that still name the old host.

1. Run `portfolio lint` from a project that might name the old host, and fix or knowingly keep each warning. See [What stays literal](#what-stays-literal).

## Verify the move

1. Request the public address list:

   ```bash
   curl -s https://infra-brain.devonwatkins.com/api/environments/addresses
   ```

   The endpoint needs no key. Confirm that the role's old addresses appear under `retired` and the new addresses appear under `current`.

1. Wait five minutes. App Brain and Code Brain cache the list for 300 seconds, and they retry a failed fetch after 30 seconds.

1. Probe one write on each brain with a record that names an old address:

   - Infra Brain: `add_lesson`
   - App Brain: `capture_knowledge`
   - Code Brain: `add_lesson`

   Each write must come back as `proposed` with `conflict_kind` set to `retired_address`. Infra Brain reads the registry directly, so its probe works at once. App Brain and Code Brain need the five-minute wait.

1. Reject all three probe records so they don't stay in the review queue. Approving a record that holds a retired address needs `acknowledge_conflict=True`, so a rejection is the only action you need here.

## What happens to later writes

Once a role has retired addresses, the brains behave as follows:

- A new record that names a retired address is held as `proposed` with `conflict_kind` set to `retired_address`.
- An in-place edit that names a retired address is refused unless you pass `allow_retired=True`. The edits are Infra Brain `update_rule`, App Brain `update_app`, and Code Brain `add_road` and `update_road`.

Use `allow_retired=True` only when the record is about the retired host, for example a migration note.

## What stays literal

Don't replace dated history with the new address. A sentence such as "Moved from Hetzner on 2026-10-23" is true as written, and rewriting it destroys the record.

Phase 3 of the environment-roles work, the infrastructure session that covers machine-level names, will add the list of references that must stay literal. Until that list exists, treat only dated history as literal and ask the registry owner (the approver) about anything else.

## Related

- Design: `docs/superpowers/specs/2026-11-07-environment-roles-design.md`
- Plan: `docs/superpowers/plans/2026-11-07-environment-roles.md`
