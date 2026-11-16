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
import { TOOL_DEFINITIONS, TOOL_NAMES, executeTool } from "./tools";
import { handleDevicesRequest } from "./devices";
import {
	checkAuth,
	checkRateLimit,
	RATE_LIMITS,
} from "./security";

// Model ID for Workers AI model
// https://developers.cloudflare.com/workers-ai/models/
const MODEL_ID = "@cf/openai/gpt-oss-20b";

// Reasoning effort for GPT-OSS-20B: "low" | "medium" | "high".
// "medium" is Cloudflare's default: a balance between answer quality
// and speed/cost. "low" answers faster with fewer reasoning tokens;
// "high" reasons longer and costs more. Reasoning cannot be disabled.
const REASONING_EFFORT = "medium" as const;

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

Tools:
- You have tools: get_weather (current weather for any city),
  search_web (look up a topic on Wikipedia), calculate (maths),
  get_datetime (current date and time), get_device_status
  (the paired Windows laptop: is it on, CPU, memory, disks,
  network, from the companion app's latest heartbeat),
  run_device_command (open an approved app or search an
  approved folder on the paired laptop).
- Use a tool when the user asks for the weather, a fact you may not
  know, a calculation, or the time or date. Do not guess these.
- Use get_device_status when the user asks about their laptop,
  computer, or PC, or whether it is on. If it says the laptop is
  off or not paired, say so plainly and say what to do: run the
  companion app on the laptop and pair it.
- Use run_device_command only when the user asks you to do
  something on their laptop: open an app, or find a file in a
  folder. The companion app only allows what the user has
  approved; if it refuses, tell the user the app or folder is
  not on the approved list and how to add it (right-click the
  companion's tray icon, then "Allowed apps & folders"). Never
  try to run anything the user did not ask for, and never invent
  an app or folder name.
- After a tool returns a result, answer from that result in plain
  words. Never claim a tool result you did not actually get.

Identity:
- Your name is VESPER.
- You are the user's personal assistant.
- You run on OpenAI's GPT-OSS-20B model, served through Cloudflare
  Workers AI. If someone asks what model you are, answer honestly:
  GPT-OSS-20B. Never claim to be GPT-4, ChatGPT, or any other model.
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
					"Access-Control-Allow-Methods": "POST, GET, OPTIONS",
					"Access-Control-Allow-Headers":
						"Content-Type, X-Vesper-Key, X-Vesper-Device",
				},
			});
		}

		// Handle static assets (frontend)
		if (url.pathname === "/" || !url.pathname.startsWith("/api/")) {
			return env.ASSETS.fetch(request);
		}

		// API Routes
		if (url.pathname === "/api/model-info") {
			// Diagnostic endpoint: reports the server-side model
			// configuration. No secrets or credentials are exposed.
			if (request.method === "GET") {
				return new Response(
					JSON.stringify({
						assistant: "VESPER",
						provider: "Cloudflare Workers AI",
						configured_model: MODEL_ID,
						reasoning_effort: REASONING_EFFORT,
						streaming: true,
						tools: TOOL_NAMES,
						auth: "x-vesper-key",
						rate_limits: RATE_LIMITS,
						memory: env.DB ? "d1" : "disabled",
						status: "configured",
					}),
					{
						headers: {
							"content-type": "application/json",
							"Access-Control-Allow-Origin": "*",
						},
					},
				);
			}

			// Method not allowed for other request types
			return new Response("Method not allowed", { status: 405 });
		}

		if (url.pathname === "/api/chat") {
			// Handle POST requests for chat
			if (request.method === "POST") {
				return handleChatRequest(request, env, ctx);
			}

			// Method not allowed for other request types
			return new Response("Method not allowed", { status: 405 });
		}

		if (url.pathname === "/api/backup") {
			// Full memory export as JSON, protected by the same key
			// and rate limit as chat. GET only.
			if (request.method !== "GET") {
				return new Response("Method not allowed", { status: 405 });
			}
			return handleBackupRequest(request, env);
		}

		if (url.pathname.startsWith("/api/devices/")) {
			// Windows Companion device endpoints (pair, confirm,
			// heartbeat, command queue, results). Auth is handled
			// inside: site key for pair/confirm, device secret
			// for the rest.
			return handleDevicesRequest(request, env);
		}

		// Handle 404 for unmatched routes
		return new Response("Not found", { status: 404 });
	},
} satisfies ExportedHandler<Env>;

/**
 * Shape of the non-streaming reply used to decide whether a tool
 * is needed. env.AI.run returns a union of output types, so the
 * tool-decision reply is read through this narrow shape.
 */
type DecisionResponse = {
	choices?: Array<{
		message?: {
			content?: string | null;
			tool_calls?: Array<{
				id: string;
				type: string;
				function: { name: string; arguments: string };
			}>;
		};
		finish_reason?: string;
	}>;
};

/**
 * Handles chat API requests.
 *
 * Two steps, so the model can use tools:
 * 1. A short non-streaming call with the tools attached. The model
 *    either answers directly or asks for tool calls.
 * 2. If tools were asked for, run them (at most 3, one round only),
 *    then stream the final answer with the results included.
 * When no tool is needed, the first call's own text is streamed back
 * as SSE, so the frontend contract never changes.
 */
async function handleChatRequest(
	request: Request,
	env: Env,
	ctx: ExecutionContext,
): Promise<Response> {
	try {
		// Phase 7 protection, before any model work: rate limit first
		// (per IP, so one abusive address cannot flood the quota),
		// then the API key.
		const limit = await checkRateLimit(request, env);
		if (!limit.ok) {
			return new Response(
				JSON.stringify({
					error: "Too many requests. Please wait a minute and try again.",
				}),
				{
					status: 429,
					headers: {
						"content-type": "application/json",
						"Retry-After": String(limit.retryAfter ?? 60),
						"Access-Control-Allow-Origin": "*",
					},
				},
			);
		}
		const auth = await checkAuth(request, env);
		if (!auth.ok) {
			return new Response(JSON.stringify({ error: "Unauthorized" }), {
				status: 401,
				headers: {
					"content-type": "application/json",
					"Access-Control-Allow-Origin": "*",
				},
			});
		}

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

		// Step 1: tool decision (non-streaming, small budget)
		const decision = (await env.AI.run<typeof MODEL_ID>(MODEL_ID, {
			messages: modelMessages,
			tools: TOOL_DEFINITIONS,
			tool_choice: "auto",
			max_tokens: 512,
			reasoning_effort: REASONING_EFFORT,
		})) as unknown as DecisionResponse;

		const decisionMessage = decision.choices?.[0]?.message;
		const toolCalls = (decisionMessage?.tool_calls ?? []).filter(
			(call) => call.type === "function",
		);
		const decisionText = decisionMessage?.content ?? "";

		let clientStream: ReadableStream<Uint8Array>;
		if (toolCalls.length === 0) {
			// No tool needed: stream the decision's own text as SSE
			clientStream = textToSseStream(
				decisionText ||
					"Sorry, I didn't manage to answer that. Could you try again?",
			);
		} else {
			// Step 2: run the tools, then stream the final answer
			const calls = toolCalls.slice(0, 3);
			const toolMessages: ChatMessage[] = [];
			for (const call of calls) {
				let args: Record<string, unknown> = {};
				try {
					args = JSON.parse(call.function.arguments || "{}") as Record<
						string,
						unknown
					>;
				} catch {
					args = {};
				}
				const result = await executeTool(call.function.name, args, env);
				toolMessages.push({
					role: "tool",
					content: result,
					tool_call_id: call.id,
				});
			}
			const assistantMessage: ChatMessage = {
				role: "assistant",
				content: decisionText || "",
				tool_calls: calls.map((call) => ({
					id: call.id,
					type: "function" as const,
					function: {
						name: call.function.name,
						arguments: call.function.arguments,
					},
				})),
			};
			const stream = await env.AI.run<typeof MODEL_ID>(
				MODEL_ID,
				{
					messages: [...modelMessages, assistantMessage, ...toolMessages],
					max_tokens: 1024,
					reasoning_effort: REASONING_EFFORT,
					stream: true,
				},
				{
					// Uncomment to use AI Gateway
					// gateway: {
					//   id: "YOUR_GATEWAY_ID", // Replace with your AI Gateway ID
					//   skipCache: false,      // Set to true to bypass cache
					//   cacheTtl: 3600,        // Cache time-to-live in seconds
					// },
				},
			);
			clientStream = stream as ReadableStream<Uint8Array>;
		}

		// Split the stream: one branch goes to the browser, the other is
		// read in the background so the full reply can be saved to memory
		const [clientBranch, memoryBranch] = clientStream.tee();
		ctx.waitUntil(
			saveConversation(env, conversation_id, messages, memoryBranch).catch(
				(err) => console.error("Failed to save conversation:", err),
			),
		);

		return new Response(clientBranch, {
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

/**
 * Exports the whole D1 memory table as JSON. Same protection as
 * chat: rate limit first, then the API key. GET only.
 */
async function handleBackupRequest(
	request: Request,
	env: Env,
): Promise<Response> {
	const jsonHeaders = {
		"content-type": "application/json",
		"Access-Control-Allow-Origin": "*",
	};
	try {
		const limit = await checkRateLimit(request, env);
		if (!limit.ok) {
			return new Response(
				JSON.stringify({
					error: "Too many requests. Please wait a minute and try again.",
				}),
				{
					status: 429,
					headers: {
						...jsonHeaders,
						"Retry-After": String(limit.retryAfter ?? 60),
					},
				},
			);
		}
		const auth = await checkAuth(request, env);
		if (!auth.ok) {
			return new Response(JSON.stringify({ error: "Unauthorized" }), {
				status: 401,
				headers: jsonHeaders,
			});
		}
		if (!env.DB) {
			return new Response(
				JSON.stringify({ exported_at: new Date().toISOString(), count: 0, messages: [] }),
				{ headers: jsonHeaders },
			);
		}
		const rows = await env.DB.prepare(
			"SELECT id, conversation_id, role, content, created_at FROM messages ORDER BY id ASC LIMIT 5000",
		).all<{
			id: number;
			conversation_id: string;
			role: string;
			content: string;
			created_at: number;
		}>();
		const messages = rows.results ?? [];
		return new Response(
			JSON.stringify({
				exported_at: new Date().toISOString(),
				count: messages.length,
				messages,
			}),
			{ headers: jsonHeaders },
		);
	} catch (error) {
		console.error("Error processing backup request:", error);
		return new Response(JSON.stringify({ error: "Failed to export" }), {
			status: 500,
			headers: jsonHeaders,
		});
	}
}

/**
 * Wraps plain text in the same SSE shape the streaming replies use,
 * so the frontend can read it unchanged.
 */
function textToSseStream(text: string): ReadableStream<Uint8Array> {
	const encoder = new TextEncoder();
	const payload =
		`data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n` +
		"data: [DONE]\n\n";
	return new ReadableStream<Uint8Array>({
		start(controller) {
			controller.enqueue(encoder.encode(payload));
			controller.close();
		},
	});
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
