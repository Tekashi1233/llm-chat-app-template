# N2 read endpoints — approval request

Branch: `n2-read-endpoints`. Base: `main` at the N2 app commit.
**Not deployed.** Merging this branch to `main` deploys it (the repo
auto-deploys the Worker on push to `main`). Merge only after the
owner approves.

## Why these exist

The N2 Command Centre's Memory Vault, Devices dashboard and Activity
timeline need to READ three things the Worker already holds. No
existing endpoint exposes them to the app:

| Section | Data | Existing route? |
|---|---|---|
| Memory Vault | stored memories for one conversation | No — memories are only injected into chat prompts |
| Devices | latest paired-device heartbeat | No — only the model can read it, via the `get_device_status` tool |
| Activity | `device_actions` audit rows | No — only the model can read them, via `get_action_history` |

The Conversations list and the Tools dashboard need no new endpoint
(`/api/backup` and `/api/model-info` already cover them).

## The three endpoints (all GET, all read-only)

### 1. `GET /api/memories?conversation_id=<id>`
- Auth: `X-Vesper-Key` site key + the normal rate limit (same as `/api/backup`).
- Returns: `{ conversation_id, count, memories: [{ id, kind, topic, fact, project, source, created_at, updated_at, superseded_by, status }] }`, newest first, max 200.
- `conversation_id` is required (400 without it). Scoped to that one
  conversation — the P2-M1 rule: memories never carry across
  browsers, devices or conversations.
- Reads the existing `memories` table via a new `listMemories()`
  in `src/memory.ts`. No schema change, no migration.

### 2. `GET /api/devices/status`
- Auth: `X-Vesper-Key` site key + the normal rate limit (same helper
  the pair/confirm endpoints use: `requireSiteAccess`).
- Returns: exactly the JSON `getDeviceStatus()` already builds for
  the model — `{ online, device, reason?, last_seen_seconds_ago?,
  cpu_percent?, ram_used_gb?, ram_total_gb?, disks?, network?,
  uptime_hours? }`. No new logic, no new data.

### 3. `GET /api/devices/activity?limit=50`
- Auth: `X-Vesper-Key` site key + the normal rate limit.
- Returns: `{ device, count, actions: [{ kind, detail, approved,
  created_at }] }`, newest first, for the latest paired device.
  `limit` is clamped to 1–100 (default 50).
- Reads the existing `device_actions` audit rows as stored. No
  resolution of old `command_result` rows (the model-facing
  `getActionHistory` keeps its own resolution; the app shows the
  stored rows verbatim).

## What this does NOT change

- No writes of any kind (no POST/PUT/DELETE, no D1 writes).
- No D1 migration, no schema change, no data change.
- No secret change, no new secret, no auth-model change — the same
  shared site key and the same rate limits as `/api/backup`.
- No change to chat, memory storage, correction, dedup, redaction,
  validation, tools, Companion pairing, or the website.
- The Worker name, URL and all existing routes are untouched.
- `/api/backup` keeps its existing behaviour (it already exports all
  conversations under the same key; these endpoints expose strictly
  less than it does).

## Files changed (this branch vs main)

- `src/memory.ts` — new exported `listMemories(env, conversationId)`.
- `src/index.ts` — `GET /api/memories` route + `handleMemoriesRequest`
  (rate limit + key check mirrored from `handleBackupRequest`).
- `src/devices.ts` — `GET /api/devices/status` + `GET
  /api/devices/activity` routes and handlers (via `requireSiteAccess`).

## Verification (after an approved merge)

1. `npm run check` — already exit 0 on this branch.
2. After the auto-deploy lands (~60–90 s), probe with the site key:
   - `GET /api/memories?conversation_id=<the app's id>` → 200, memories array.
   - `GET /api/memories` (no id) → 400.
   - `GET /api/devices/status` → 200, `{ online: true, device: "DESKTOP-9NRU515", … }` while the Companion runs.
   - `GET /api/devices/activity` → 200, actions newest first.
   - All four without the key → 401.
3. In the N2 app, the Memory Vault, Devices and Activity sections
   stop showing "not enabled yet" and show live data. The N1 chat
   regression (send + follow-up) is re-run.

## Rollback

Revert the merge commit on `main` and push. The Worker redeploys
without the three routes (they 404 again) and the app falls back to
its "not enabled yet" states. No data is touched by the revert.
