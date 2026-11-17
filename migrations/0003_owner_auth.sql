-- N5 M2: owner auth (2026-11-17).
--
-- One owner, password login + 30-day sliding session tokens.
-- Passwords are PBKDF2 (WebCrypto, 210,000 iterations, SHA-256,
-- per-owner random 16-byte salt), stored as
-- "pbkdf2$210000$<salt-b64>$<hash-b64>". Tokens are 256-bit random
-- values; only their SHA-256 hash is stored in `sessions`.
--
-- DEPLOYMENT ORDER: apply this migration BEFORE deploying the M2
-- Worker code. The code selects/inserts owner_id on messages,
-- memories, devices, device_commands and device_actions; without
-- the columns those inserts fail (chat still answers, but nothing
-- is saved) and the auth endpoints fail outright. Apply via the
-- D1 API (wrangler.jsonc has no migrations_dir):
--   wrangler d1 execute vesper-memory --file migrations/0003_owner_auth.sql
-- Then verify:
--   SELECT name FROM sqlite_master WHERE type='table'
--     AND name IN ('owners','sessions','auth_audit');
--   SELECT owner_id, COUNT(*) FROM messages GROUP BY owner_id;
--
-- OWNER ID: the single owner is id 'owner-1'. The backfill below
-- stamps every existing row with 'owner-1' (the owners row itself
-- is created later, by POST /api/auth/setup, with that same id —
-- the password is set by the owner at first-run setup, never here
-- and never in this file). There is no foreign-key enforcement, so
-- the backfill works before the owners row exists. If a second
-- owner is ever added (family phase), its rows carry its own id.
--
-- Idempotency: CREATE TABLE / CREATE INDEX use IF NOT EXISTS.
-- The ALTER TABLE ... ADD COLUMN statements are NOT idempotent:
-- re-running them fails with "duplicate column name". Do not
-- re-run after this migration has been applied. Check first:
--   SELECT sql FROM sqlite_master WHERE type='table'
--     AND name IN ('messages','memories','devices',
--                  'device_commands','device_actions');

CREATE TABLE IF NOT EXISTS owners (
  id TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  device_label TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_owner
  ON sessions (owner_id, revoked);

CREATE TABLE IF NOT EXISTS auth_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id TEXT,
  event TEXT NOT NULL,
  ip TEXT,
  created_at INTEGER NOT NULL,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_audit_owner
  ON auth_audit (owner_id, created_at);

-- Owner binding on the existing tables. New rows always carry the
-- owner's id from the validated token (Bearer) or the single owner
-- (site-key grace period, once an owner exists). A request for
-- another owner's conversation_id returns 404, not 403.
ALTER TABLE messages ADD COLUMN owner_id TEXT;
ALTER TABLE memories ADD COLUMN owner_id TEXT;
ALTER TABLE devices ADD COLUMN owner_id TEXT;
ALTER TABLE device_commands ADD COLUMN owner_id TEXT;
ALTER TABLE device_actions ADD COLUMN owner_id TEXT;

-- Backfill: every row written before owner auth belongs to the
-- single owner. Runs before the owners row exists (see header).
UPDATE messages SET owner_id = 'owner-1' WHERE owner_id IS NULL;
UPDATE memories SET owner_id = 'owner-1' WHERE owner_id IS NULL;
UPDATE devices SET owner_id = 'owner-1' WHERE owner_id IS NULL;
UPDATE device_commands SET owner_id = 'owner-1' WHERE owner_id IS NULL;
UPDATE device_actions SET owner_id = 'owner-1' WHERE owner_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_messages_owner
  ON messages (owner_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_memories_owner
  ON memories (owner_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_devices_owner
  ON devices (owner_id, status);
CREATE INDEX IF NOT EXISTS idx_device_commands_owner
  ON device_commands (owner_id, device_id);
CREATE INDEX IF NOT EXISTS idx_device_actions_owner
  ON device_actions (owner_id, device_id);
