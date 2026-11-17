/**
 * VESPER P2 — Advanced Memory (Milestone 1).
 *
 * Long-term memory: durable facts, preferences, project facts and
 * ongoing plans, stored per browser conversation and recalled into
 * the model prompt. Plus the credential guard that protects BOTH
 * the new memories table and the raw messages table (Phase 3):
 * anything that looks like a password, key, token or secret is
 * redacted before it is written to D1.
 *
 * Scope and honesty notes (approved by the user, 2026-11-17):
 * - Memory is keyed by conversation_id — the same per-browser id
 *   the frontend keeps in localStorage. It does NOT carry to other
 *   browsers, incognito windows, or devices. There is no account,
 *   so there is no cross-device memory, and a cleared browser
 *   starts with no memory.
 * - Extraction is conservative: only explicit "remember that..."
 *   requests, durable preferences, important project facts and
 *   ongoing plans. Never casual chat, one-off questions,
 *   credentials or sensitive personal information.
 * - Every memory records WHEN it was stored and WHETHER the user
 *   said it directly (source "user") or VESPER inferred it
 *   (source "inferred"), so VESPER can explain what it remembers
 *   and why.
 *
 * Every function here is fail-open: on any D1 error it returns an
 * empty result or an error string, and chat continues exactly as
 * it did before P2.
 */

import type { Env } from "./types";

// ---------------------------------------------------------------------------
// Credential detection and redaction
// ---------------------------------------------------------------------------
//
// Two uses:
// - redactCredentials(text): messages table. The message is still
//   stored, but the secret value is replaced with [redacted] first.
//   The live reply the user sees is NOT changed — only the copy
//   written to D1.
// - containsCredential(text): memories table. A "fact" that IS a
//   credential has no safe form, so remember_fact refuses to store
//   it at all instead of redacting it.

const REDACTED = "[redacted]";

/** Patterns that identify a secret value. Each is replaced, never kept. */
const SECRET_PATTERNS: RegExp[] = [
	// Private key blocks (whole block, including newlines).
	/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
	// JSON Web Tokens: three base64url segments.
	/\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g,
	// Labelled secrets: password / api key / secret / token / bearer.
	// The value group stops at whitespace or trailing punctuation,
	// so "password is hunter2," keeps its comma.
	/\b(password|passwd|pwd|api[-_ ]?key|apikey|secret|auth[-_ ]?token|access[-_ ]?token|bearer)\b(\s*(is|:|=)\s*)([^\s,;]+)/gi,
	// Known provider key prefixes.
	/\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})\b/g,
	// A 64-hex string (the shape of a VESPER device secret).
	/\b[0-9a-f]{64}\b/g,
	// Long mixed-case-and-digit tokens (32+ chars, e.g. pasted API keys).
	// Requires both upper/lower case and a digit, so ordinary long
	// words, URLs and hashes of plain text do not match.
	/\b(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{32,}\b/g,
];

/** True when the text holds anything that looks like a credential. */
export function containsCredential(text: string): boolean {
	if (!text) return false;
	return SECRET_PATTERNS.some((pattern) => {
		pattern.lastIndex = 0;
		return pattern.test(text);
	});
}

/**
 * Returns the text with every secret value replaced by [redacted].
 * Labels are kept ("password is [redacted]") so the stored message
 * still reads naturally. Returns the input unchanged when nothing
 * matches.
 */
export function redactCredentials(text: string): string {
	if (!text) return text;
	let out = text;
	for (const pattern of SECRET_PATTERNS) {
		pattern.lastIndex = 0;
		out = out.replace(pattern, (match, ...groups) => {
			// The labelled-secret pattern captures (label)(separator)
			// (inner)(value): keep label + separator, redact only the
			// value. Every other pattern has no (or different) groups,
			// so check the TYPES, not just that the slots are filled:
			// replace() appends the match offset (a number) and the
			// whole string after the captures, and those must never
			// be mistaken for a label.
			const label = groups[0];
			const separator = groups[1];
			if (typeof label === "string" && typeof separator === "string") {
				return `${label}${separator}${REDACTED}`;
			}
			return REDACTED;
		});
	}
	return out;
}

// ---------------------------------------------------------------------------
// Memories
// ---------------------------------------------------------------------------

export type MemoryKind = "fact" | "preference" | "project" | "plan";

export type Memory = {
	id: number;
	conversation_id: string;
	kind: MemoryKind;
	topic: string;
	fact: string;
	project: string | null;
	source: "user" | "inferred";
	created_at: number;
	updated_at: number;
	superseded_by: number | null;
	status: "active" | "superseded" | "deleted";
};

const MEMORY_KINDS: readonly string[] = ["fact", "preference", "project", "plan"];
const MAX_FACT_CHARS = 500;
const MAX_TOPIC_CHARS = 80;
const MAX_PROJECT_CHARS = 40;
const RECALL_LIMIT = 8;

/** Words too common to score on when matching a question to memories. */
const STOPWORDS = new Set([
	"the", "a", "an", "is", "are", "was", "were", "what", "how", "do",
	"does", "did", "you", "your", "me", "my", "i", "it", "of", "to",
	"in", "on", "for", "and", "or", "can", "could", "please", "tell",
	"say", "know", "about", "that", "this", "with", "have", "has",
	"when", "where", "who", "why", "there", "here", "yourself",
]);

function keywords(text: string): string[] {
	return text
		.toLowerCase()
		.split(/[^a-z0-9]+/)
		.filter((word) => word.length > 2 && !STOPWORDS.has(word));
}

type MemoryRow = {
	id: number;
	kind: string;
	topic: string;
	fact: string;
	project: string | null;
	source: string;
	created_at: number;
	updated_at: number;
	superseded_by: number | null;
	status: string;
};

function toMemory(row: MemoryRow, conversationId: string): Memory {
	return {
		id: row.id,
		conversation_id: conversationId,
		kind: (MEMORY_KINDS.includes(row.kind) ? row.kind : "fact") as MemoryKind,
		topic: row.topic,
		fact: row.fact,
		project: row.project,
		source: row.source === "inferred" ? "inferred" : "user",
		created_at: row.created_at,
		updated_at: row.updated_at,
		superseded_by: row.superseded_by,
		status: row.status === "superseded" ? "superseded" : "active",
	};
}

/**
 * Loads the memories most relevant to the user's current message.
 * Keyword overlap on the topic counts most, then the fact text,
 * then a project-name match; ties break by recency. Returns at
 * most RECALL_LIMIT memories. Never throws: on any failure it
 * returns [] and chat continues without long-term memory.
 */
export async function loadMemories(
	env: Env,
	conversationId: string,
	queryText: string,
): Promise<Memory[]> {
	try {
		if (!env.DB) return [];
		const rows = await env.DB.prepare(
			"SELECT id, kind, topic, fact, project, source, created_at, updated_at, superseded_by, status FROM memories WHERE conversation_id = ?1 AND status = 'active' ORDER BY updated_at DESC LIMIT 50",
		)
			.bind(conversationId)
			.all<MemoryRow>();
		const memories = (rows.results ?? []).map((row) =>
			toMemory(row, conversationId),
		);
		if (memories.length === 0) return [];
		const queryWords = keywords(queryText);
		if (queryWords.length === 0) {
			// No meaningful words to match on: the most recent few.
			return memories.slice(0, RECALL_LIMIT);
		}
		const scored = memories.map((memory) => {
			let score = 0;
			const topicWords = keywords(memory.topic);
			const factWords = keywords(memory.fact);
			for (const word of queryWords) {
				if (topicWords.some((t) => t.includes(word) || word.includes(t))) score += 3;
				else if (factWords.some((t) => t.includes(word) || word.includes(t))) score += 1;
				if (
					memory.project &&
					memory.project.toLowerCase().includes(word)
				) {
					score += 2;
				}
			}
			return { memory, score };
		});
		scored.sort(
			(a, b) =>
				b.score - a.score || b.memory.updated_at - a.memory.updated_at,
		);
		// Only memories that matched something, or — when nothing
		// matched at all — the single most recent one, so the model
		// is not flooded with irrelevant facts.
		const matched = scored.filter((item) => item.score > 0);
		if (matched.length === 0) return [scored[0].memory];
		return matched.slice(0, RECALL_LIMIT).map((item) => item.memory);
	} catch (error) {
		console.error("Failed to load memories:", error);
		return [];
	}
}

/**
 * Stores a memory, with dedup and conflict handling:
 * - Same topic + kind, same fact: already remembered — only the
 *   timestamp is touched, no duplicate row.
 * - Same topic + kind, different fact: the old row is marked
 *   superseded and points at the new row; the new fact wins.
 * Refuses outright when the fact contains a credential.
 * Returns a short JSON string for the model.
 */
export async function storeMemory(
	env: Env,
	conversationId: string,
	args: {
		kind?: unknown;
		topic?: unknown;
		fact?: unknown;
		project?: unknown;
		source?: unknown;
	},
): Promise<string> {
	try {
		if (!env.DB) {
			return JSON.stringify({ error: "Memory storage is unavailable." });
		}
		const kind =
			typeof args.kind === "string" && MEMORY_KINDS.includes(args.kind)
				? (args.kind as MemoryKind)
				: "fact";
		const topic =
			typeof args.topic === "string" ? args.topic.trim() : "";
		const fact = typeof args.fact === "string" ? args.fact.trim() : "";
		const project =
			typeof args.project === "string" && args.project.trim()
				? args.project.trim().toLowerCase()
				: null;
		const source =
			args.source === "inferred" ? "inferred" : "user";
		if (!topic || !fact) {
			return JSON.stringify({
				error: "A memory needs both a short topic and the fact itself.",
			});
		}
		if (topic.length > MAX_TOPIC_CHARS || fact.length > MAX_FACT_CHARS) {
			return JSON.stringify({
				error: "That memory is too long to store. Keep the fact to one or two sentences.",
			});
		}
		if (project && project.length > MAX_PROJECT_CHARS) {
			return JSON.stringify({ error: "That project name is too long." });
		}
		// Credentials are never stored as memories. There is no safe
		// redacted form of a fact that IS a secret, so refuse it.
		if (containsCredential(fact) || containsCredential(topic)) {
			return JSON.stringify({
				error:
					"I don't store passwords, API keys, tokens or other secrets. I didn't remember that.",
			});
		}
		const now = Date.now();
		const existing = await env.DB.prepare(
			"SELECT id, fact FROM memories WHERE conversation_id = ?1 AND status = 'active' AND kind = ?2 AND lower(topic) = lower(?3) ORDER BY updated_at DESC LIMIT 1",
		)
			.bind(conversationId, kind, topic)
			.first<{ id: number; fact: string }>();
		if (existing) {
			if (existing.fact.trim().toLowerCase() === fact.toLowerCase()) {
				// Same fact again: no duplicate, just refresh the date.
				await env.DB.prepare(
					"UPDATE memories SET updated_at = ?1 WHERE id = ?2",
				)
					.bind(now, existing.id)
					.run();
				return JSON.stringify({
					stored: false,
					already_remembered: true,
					topic,
					note: "That was already remembered, so nothing changed.",
				});
			}
			// A different fact for the same topic: the new one wins,
			// the old one is kept as superseded history.
			const inserted = await env.DB.prepare(
				"INSERT INTO memories (conversation_id, kind, topic, fact, project, source, created_at, updated_at, status) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'active')",
			)
				.bind(conversationId, kind, topic, fact, project, source, now, now)
				.run();
			const newId = Number(inserted.meta?.last_row_id ?? 0);
			if (newId) {
				await env.DB.prepare(
					"UPDATE memories SET status = 'superseded', superseded_by = ?1 WHERE id = ?2",
				)
					.bind(newId, existing.id)
					.run();
			}
			return JSON.stringify({
				stored: true,
				topic,
				superseded_previous: true,
				note: "This replaces what was remembered about that topic before.",
			});
		}
		await env.DB.prepare(
			"INSERT INTO memories (conversation_id, kind, topic, fact, project, source, created_at, updated_at, status) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'active')",
		)
			.bind(conversationId, kind, topic, fact, project, source, now, now)
			.run();
		return JSON.stringify({ stored: true, topic });
	} catch (error) {
		console.error("Failed to store memory:", error);
		return JSON.stringify({ error: "Could not store that memory." });
	}
}

/**
 * Formats recalled memories for the system prompt, with the
 * transparency the user asked for: what it is, which project it
 * belongs to, when it was recorded, and whether the user said it
 * directly or VESPER inferred it. Returns "" when there is nothing.
 */
export function formatMemoriesForPrompt(memories: Memory[]): string {
	if (memories.length === 0) return "";
	const lines = memories.map((memory) => {
		const date = new Date(memory.created_at).toISOString().slice(0, 10);
		const origin =
			memory.source === "user"
				? "they told you directly"
				: "you inferred it";
		const project = memory.project ? ` [project: ${memory.project}]` : "";
		return `- ${memory.topic}: ${memory.fact}${project} (recorded ${date}, ${origin})`;
	});
	return (
		"\n\nWhat you remember about the user (long-term memory, this browser conversation only):\n" +
		lines.join("\n") +
		"\nWhen the user asks what you remember, answer from this list and say when each memory was recorded and whether they told you directly or you inferred it."
	);
}
