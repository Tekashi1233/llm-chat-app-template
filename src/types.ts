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
	| { role: "system" | "user"; content: string }
	| { role: "assistant"; content: string; tool_calls?: ToolCall[] }
	| { role: "tool"; content: string; tool_call_id: string };
