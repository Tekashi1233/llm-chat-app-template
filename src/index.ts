/**
 * LLM Chat Application Template
 *
 * A simple chat application using Cloudflare Workers AI.
 * This template demonstrates how to implement an LLM-powered chat interface with
 * streaming responses using Server-Sent Events (SSE).
 *
 * @license MIT
 */
import { Env, ChatMessage } from "./types";

// Model ID for Workers AI model
// https://developers.cloudflare.com/workers-ai/models/
const MODEL_ID = "@cf/openai/gpt-oss-20b";

// Default system prompt
const SYSTEM_PROMPT = `
You are VESPER, a highly capable personal AI assistant.

Personality:
- Speak in natural British English.
- Use a composed, intelligent, confident tone.
- Be concise when possible and detailed when necessary.
- Address the user respectfully.
- Avoid unnecessary disclaimers and repetitive explanations.

Behaviour:
- Think carefully before answering.
- Be honest about uncertainty.
- Follow the user's instructions where appropriate.
- Help with programming, automation, research and technical projects.
- Never claim to have performed an action you haven't performed.
- You can see earlier messages from this conversation; use them when
  relevant instead of asking the user to repeat things.

Identity:
- Your name is VESPER.
- You are the user's personal assistant.
`;

export default {
	/**
	 * Main request handler for the Worker
	 */
	async fetch(
		request: Request,
		env: Env,
		ctx: ExecutionContext,
	): Promise<Response> {
		const url = new URL(request.url);

		// Allow the VESPER website to call the API from its own domain
		if (request.method === "OPTIONS") {
			return new Response(null, {
				headers: {
					"Access-Control-Allow-Origin": "*",
					"Access-Control-Allow-Methods": "POST, OPTIONS",
					"Access-Control-Allow-Headers": "Content-Type",
				},
			});
		}

		// Handle static assets (frontend)
		if (url.pathname === "/" || !url.pathname.startsWith("/api/")) {
			return env.ASSETS.fetch(request);
		}

		// API Routes
		if (url.pathname === "/api/chat") {
			// Handle POST requests for chat
			if (request.method === "POST") {
				return handleChatRequest(request, env, ctx);
			}

			// Method not allowed for other request types
			return new Response("Method not allowed", { status: 405 });
		}

		// Handle 404 for unmatched routes
		return new Response("Not found", { status: 404 });
	},
} satisfies ExportedHandler<Env>;

/**
 * Handles chat API requests
 */
async function handleChatRequest(
	request: Request,
	env: Env,
	ctx: ExecutionContext,
): Promise<Response> {
	try {
		// Parse JSON request body
		const { messages = [], conversation_id = "default" } = (await request.json()) as {
			messages: ChatMessage[];
			conversation_id?: string;
		};

		// Load recent conversation history from D1 memory
		const history = await loadHistory(env, conversation_id);

		// Build the messages the model sees: system prompt, remembered
		// history, then the new message(s) from this request
		const modelMessages: ChatMessage[] = [];
		if (!messages.some((msg) => msg.role === "system")) {
			modelMessages.push({ role: "system", content: SYSTEM_PROMPT });
		}
		modelMessages.push(...history);
		modelMessages.push(...messages);

		const inputs = {
			messages: modelMessages,
			max_tokens: 1024,
			stream: true,
		} satisfies AiTextGenerationInput & { stream: true };

		const stream = await env.AI.run<typeof MODEL_ID>(MODEL_ID, inputs, {
			// Uncomment to use AI Gateway
			// gateway: {
			//   id: "YOUR_GATEWAY_ID", // Replace with your AI Gateway ID
			//   skipCache: false,      // Set to true to bypass cache
			//   cacheTtl: 3600,        // Cache time-to-live in seconds
			// },
		});

		// Split the stream: one branch goes to the browser, the other is
		// read in the background so the full reply can be saved to memory
		const [clientStream, memoryStream] = (
			stream as ReadableStream<Uint8Array>
		).tee();
		ctx.waitUntil(
			saveConversation(env, conversation_id, messages, memoryStream).catch(
				(err) => console.error("Failed to save conversation:", err),
			),
		);

		return new Response(clientStream, {
			headers: {
				"content-type": "text/event-stream; charset=utf-8",
				"cache-control": "no-cache",
				connection: "keep-alive",
				"Access-Control-Allow-Origin": "*",
			},
		});
	} catch (error) {
		console.error("Error processing chat request:", error);
		return new Response(
			JSON.stringify({ error: "Failed to process request" }),
			{
				status: 500,
				headers: { "content-type": "application/json" },
			},
		);
	}
}

// How many past messages to recall for the model (10 exchanges)
const HISTORY_LIMIT = 20;

/**
 * Loads the most recent messages of one conversation from D1.
 * Returns them oldest-first. Never throws: on any failure the chat
 * simply continues without memory.
 */
async function loadHistory(
	env: Env,
	conversationId: string,
): Promise<ChatMessage[]> {
	try {
		if (!env.DB) return [];
		const rows = await env.DB.prepare(
			"SELECT role, content FROM messages WHERE conversation_id = ?1 ORDER BY id DESC LIMIT ?2",
		)
			.bind(conversationId, HISTORY_LIMIT)
			.all<{ role: string; content: string }>();
		return (rows.results ?? [])
			.reverse()
			.filter((row) => row.role === "user" || row.role === "assistant")
			.map((row) => ({
				role: row.role as "user" | "assistant",
				content: row.content,
			}));
	} catch (error) {
		console.error("Failed to load history:", error);
		return [];
	}
}

/**
 * Saves the new user message(s) and the streamed assistant reply to D1.
 */
async function saveConversation(
	env: Env,
	conversationId: string,
	newMessages: ChatMessage[],
	stream: ReadableStream<Uint8Array>,
): Promise<void> {
	if (!env.DB) return;
	const reply = await readStreamedReply(stream);
	const now = Date.now();
	for (const msg of newMessages) {
		if (msg.role !== "user") continue;
		await env.DB.prepare(
			"INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?1, ?2, ?3, ?4)",
		)
			.bind(conversationId, msg.role, msg.content, now)
			.run();
	}
	if (reply) {
		await env.DB.prepare(
			"INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?1, ?2, ?3, ?4)",
		)
			.bind(conversationId, "assistant", reply, Date.now())
			.run();
	}
}

/**
 * Reads a streamed SSE reply and joins its text pieces into one string.
 * Handles the Workers AI chunk shapes (delta.content, choices[0].delta,
 * and plain response fields).
 */
async function readStreamedReply(
	stream: ReadableStream<Uint8Array>,
): Promise<string> {
	const reader = stream.getReader();
	const decoder = new TextDecoder();
	let buffer = "";
	let text = "";
	try {
		for (;;) {
			const { done, value } = await reader.read();
			if (done) break;
			buffer += decoder.decode(value, { stream: true });
			let newline = buffer.indexOf("\n");
			while (newline !== -1) {
				const line = buffer.slice(0, newline).trim();
				buffer = buffer.slice(newline + 1);
				if (line.startsWith("data:")) {
					const payload = line.slice(5).trim();
					if (payload && payload !== "[DONE]") {
						try {
							const json = JSON.parse(payload);
							const delta =
								json.delta?.content ??
								json.choices?.[0]?.delta?.content;
							if (typeof delta === "string") {
								text += delta;
							} else if (
								typeof json.response === "string" &&
								json.response
							) {
								text += json.response;
							}
						} catch {
							// Ignore non-JSON data lines
						}
					}
				}
				newline = buffer.indexOf("\n");
			}
		}
	} finally {
		reader.releaseLock();
	}
	return text.trim();
}
