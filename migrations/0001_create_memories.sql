-- P2-M1: long-term memory (2026-11-17).
--
-- Applied to the production D1 database `vesper-memory`
-- (1787f25a-2e49-406f-b193-bcb0fa3400de) on 2026-11-17 via the
-- Cloudflare API, before the Worker code that uses it was deployed.
-- The production schema was verified against this file on
-- 2026-11-17: table + all three indexes match exactly (the unique
-- index was added and verified the same day).
--
-- Idempotent (IF NOT EXISTS), so re-applying it on a database that
-- already has the table is a no-op. On a fresh database, apply with:
--   wrangler d1 migrations apply vesper-memory
--
-- Scope note: memories are keyed by conversation_id (the per-browser
-- id the frontend keeps in localStorage). They do NOT carry to other
-- browsers, incognito windows, or devices.

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  topic TEXT NOT NULL,
  fact TEXT NOT NULL,
  project TEXT,
  source TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  superseded_by INTEGER,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_memories_conversation
  ON memories (conversation_id, status);

CREATE INDEX IF NOT EXISTS idx_memories_topic
  ON memories (conversation_id, topic);

-- At most one ACTIVE memory per conversation + kind + topic +
-- project. This is the invariant that makes memory replacement
-- safe: two concurrent replacements cannot leave two active rows,
-- because the loser's INSERT violates this index and its batch
-- rolls back. project is NULL for unprojected memories, and NULLs
-- are distinct in a unique index, so coalesce(project, '') makes
-- NULL match NULL. lower(topic) makes the match case-insensitive,
-- the same way the dedup lookup matches.
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_active_topic
  ON memories (conversation_id, kind, lower(topic), coalesce(project, ''))
  WHERE status = 'active';
