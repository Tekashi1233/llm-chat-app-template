/**
 * Type definitions for the LLM chat application.
 */

export interface Env {
	/**
	 * Binding for the Workers AI API.
	 */
	AI: Ai;

	/**
	 * Binding for static assets.
	 */
	ASSETS: { fetch: (request: Request) => Promise<Response> };

	/**
	 * Binding for the D1 database that stores conversation memory.
	 */
	DB: D1Database;
}

/**
 * Represents a chat message. Tool messages carry the id of the tool
 * call they answer; assistant messages may carry tool calls.
 */
export type ToolCall = {
	id: string;
	type: "function";
	function: { name: string; arguments: string };
};

export type ChatMessage =
	| { role: "system"; content: string }
	| {
			role: "user";
			content: string;
			/**
			 * Provenance of a stored history row (P2-M1 rev6). Only
			 * set on rows read back from D1: "server" = the server's
			 * own streamed reply, "user" = a validated user message,
			 * "legacy" = written before provenance existed (unknown).
			 * Never sent to the model; buildModelMessages strips it.
			 */
			origin?: "server" | "user" | "legacy";
			/**
			 * N5 M2: client-asserted voice origin. True only when
			 * the message came from the app/website microphone
			 * (speech-to-text), never auto-sent. The revoke_device
			 * tool guard reads it; buildModelMessages strips it
			 * before the model sees it, so it never reaches the
			 * model context. Never persisted to D1.
			 */
			via_voice?: boolean;
	  }
	| {
			role: "assistant";
			content: string;
			tool_calls?: ToolCall[];
			origin?: "server" | "user" | "legacy";
	  }
	| { role: "tool"; content: string; tool_call_id: string };
