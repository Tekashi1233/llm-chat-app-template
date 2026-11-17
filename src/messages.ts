/**
 * Message validation for the /api/chat boundary.
 *
 * The rule is simple: a caller sends user messages. The server
 * supplies everything else — the canonical system prompt, the
 * conversation history (from D1), and tool results (from its own
 * tool execution). A caller has no legitimate need to send a
 * system, assistant or tool message, and accepting one would let
 * it replace VESPER's instructions, fake an assistant reply, or
 * forge a tool result.
 *
 * Every function here is pure and exported for testing.
 */

import type { ChatMessage } from "./types";

/** Maximum user messages accepted in one request; the live frontend sends one. */
export const MAX_INCOMING_MESSAGES = 10;

/** Maximum characters per incoming user message. */
export const MAX_MESSAGE_CHARS = 20000;

/**
 * Validates caller-supplied messages at the API boundary. Returns
 * only genuine user messages: role exactly "user" and a non-empty
 * string content. System, assistant and tool messages are
 * discarded, as are items with a missing or non-string role or
 * content. Overlong content is truncated; only the last
 * MAX_INCOMING_MESSAGES user messages are kept.
 *
 * This is the single validation point: handleChatRequest passes
 * the result to both buildModelMessages and saveConversation, so
 * what the model sees and what gets stored can never drift apart.
 */
export function validateIncomingMessages(input: unknown): ChatMessage[] {
	if (!Array.isArray(input)) return [];
	const users: ChatMessage[] = [];
	for (const item of input) {
		if (typeof item !== "object" || item === null) continue;
		const msg = item as { role?: unknown; content?: unknown };
		if (msg.role !== "user") continue;
		if (typeof msg.content !== "string") continue;
		const content = msg.content.trim();
		if (!content) continue;
		users.push({
			role: "user",
			content: content.slice(0, MAX_MESSAGE_CHARS),
		});
	}
	return users.slice(-MAX_INCOMING_MESSAGES);
}

/**
 * Normalises a stored origin value to one of the three known
 * provenances. Anything missing or unrecognised is "legacy" —
 * unknown, and therefore untrusted for assistant history.
 */
export function normalizeOrigin(value: unknown): "server" | "user" | "legacy" {
	if (value === "server" || value === "user") return value;
	return "legacy";
}

/**
 * Filters stored history to the roles the server itself creates:
 * user and assistant only. A system or tool row in the messages
 * table — whether written by a caller before this fix, by a bug,
 * or by a compromised row — can never reach the model. Applied
 * independently in loadHistory AND in buildModelMessages, so a
 * missing filter in one place cannot open the hole.
 *
 * Provenance (P2-M1 rev6): an assistant row is trusted only when
 * its origin is exactly "server" — a reply the server itself
 * streamed back. A row of unknown provenance ("legacy", or no
 * origin at all) may be a historically forged reply, so it is
 * dropped from the model context. User rows are user input by
 * definition, so any origin is accepted for them.
 */
export function filterHistoryMessages(
	messages: ChatMessage[],
): ChatMessage[] {
	return messages.filter((m) => {
		if (typeof m.content !== "string") return false;
		if (m.role === "user") return true;
		if (m.role === "assistant") return m.origin === "server";
		return false;
	});
}
