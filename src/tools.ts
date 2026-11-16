/**
 * VESPER tools (Phase 5).
 *
 * Server-side tools the model can call. All of them are free and need
 * no API key:
 * - get_weather: Open-Meteo (geocoding + forecast), no key required.
 * - search_web: Wikipedia search + article summary, no key required.
 * - calculate: a small maths parser (no eval).
 * - get_datetime: current date/time in any IANA timezone.
 * - get_device_status: the paired Windows laptop's CPU, RAM, disks
 *   and network, from its latest companion-app heartbeat (needs D1).
 *
 * Every tool returns a short JSON string the model reads and turns
 * into a plain-words answer. A tool never throws: on any failure it
 * returns an error message string instead.
 */

import type { Env } from "./types";
import { getDeviceStatus, runDeviceCommand } from "./devices";

export const TOOL_NAMES = [
	"get_weather",
	"search_web",
	"calculate",
	"get_datetime",
	"get_device_status",
	"run_device_command",
] as const;

/**
 * OpenAI-style function tool definition. This shape matches the
 * ChatCompletionFunctionTool type Workers AI expects, so the array
 * below can be passed straight to env.AI.run as `tools`.
 */
type FunctionTool = {
	type: "function";
	function: {
		name: string;
		description?: string;
		parameters?: Record<string, unknown>;
	};
};

export const TOOL_DEFINITIONS: FunctionTool[] = [
	{
		type: "function",
		function: {
			name: "get_weather",
			description:
				"Get the current weather for a city or place, anywhere in the world. Use it whenever the user asks about the weather, temperature, rain, or what it is like outside somewhere.",
			parameters: {
				type: "object",
				properties: {
					location: {
						type: "string",
						description:
							"City or place name, e.g. Riyadh, London, Lahore, Makkah",
					},
					units: {
						type: "string",
						enum: ["celsius", "fahrenheit"],
						description:
							"Temperature unit. Default is celsius.",
					},
				},
				required: ["location"],
			},
		},
	},
	{
		type: "function",
		function: {
			name: "search_web",
			description:
				"Look up a topic, person, place, or fact on Wikipedia. Use it when the user asks about something you may not know, or asks you to look something up. Returns a short summary and a link.",
			parameters: {
				type: "object",
				properties: {
					query: {
						type: "string",
						description:
							"The topic or question to look up, e.g. 'Ibn Sina' or 'Treaty of Guadalupe Hidalgo'",
					},
				},
				required: ["query"],
			},
		},
	},
	{
		type: "function",
		function: {
			name: "calculate",
			description:
				"Evaluate a maths expression exactly. Use it for any arithmetic the user asks for instead of working it out in your head.",
			parameters: {
				type: "object",
				properties: {
					expression: {
						type: "string",
						description:
							"Maths expression using + - * / % ^ ( ) and functions sqrt, abs, sin, cos, tan, log, ln, e.g. '(250 * 1.15) / 3' or 'sqrt(144) + 5^2'",
					},
				},
				required: ["expression"],
			},
		},
	},
	{
		type: "function",
		function: {
			name: "get_datetime",
			description:
				"Get the current date and time. Use it when the user asks what time it is, what day it is, or the date.",
			parameters: {
				type: "object",
				properties: {
					timezone: {
						type: "string",
						description:
							"IANA timezone, e.g. Asia/Riyadh, Europe/London, America/New_York. Default is Asia/Riyadh.",
					},
				},
				required: [],
			},
		},
	},
	{
		type: "function",
		function: {
			name: "get_device_status",
			description:
				"Check whether the user's paired Windows laptop is on and how it is doing: CPU, memory, disks and network, from the companion app's latest heartbeat. Use it when the user asks about their laptop, computer, PC, or whether it is on.",
			parameters: {
				type: "object",
				properties: {},
				required: [],
			},
		},
	},
	{
		type: "function",
		function: {
			name: "run_device_command",
			description:
				"Do something on the user's paired Windows laptop through the companion app: open an approved app, or search an approved folder for a file by name. The app only runs what the user has allowlisted; anything else is refused and the refusal is reported back. Use it only when the user asks for an action on their laptop.",
			parameters: {
				type: "object",
				properties: {
					action: {
						type: "string",
						enum: ["open_app", "search_files"],
						description: "open_app to launch an app, search_files to find files by name.",
					},
					app: {
						type: "string",
						description: "App name for open_app, e.g. 'notepad', 'calculator', 'chrome'.",
					},
					folder: {
						type: "string",
						description: "Folder for search_files: a friendly name like 'Desktop', 'Documents', 'Downloads', or a full path.",
					},
					query: {
						type: "string",
						description: "File name text to look for in search_files, e.g. 'homework' or 'report.docx'.",
					},
				},
				required: ["action"],
			},
		},
	},
];

/**
 * Runs one tool by name with parsed JSON arguments.
 * Returns a short JSON string for the model. Never throws.
 * `env` is needed by tools that read D1 (get_device_status).
 */
export async function executeTool(
	name: string,
	args: Record<string, unknown>,
	env?: Env,
): Promise<string> {
	try {
		switch (name) {
			case "get_weather":
				return await getWeather(
					String(args.location ?? ""),
					args.units === "fahrenheit" ? "fahrenheit" : "celsius",
				);
			case "search_web":
				return await searchWeb(String(args.query ?? ""));
			case "calculate":
				return calculate(String(args.expression ?? ""));
			case "get_datetime":
				return getDateTime(
					typeof args.timezone === "string" && args.timezone
						? args.timezone
						: "Asia/Riyadh",
				);
			case "get_device_status":
				if (!env) {
					return JSON.stringify({
						error: "Device status is unavailable.",
					});
				}
				return await getDeviceStatus(env);
			case "run_device_command":
				if (!env) {
					return JSON.stringify({
						error: "Device commands are unavailable.",
					});
				}
				return await runDeviceCommand(env, args);
			default:
				return JSON.stringify({ error: `Unknown tool: ${name}` });
		}
	} catch (error) {
		return JSON.stringify({
			error: `Tool ${name} failed: ${error instanceof Error ? error.message : "unknown error"}`,
		});
	}
}

// ---------------------------------------------------------------------------
// Weather (Open-Meteo, free, no key)
// ---------------------------------------------------------------------------

const WMO_DESCRIPTIONS: Record<number, string> = {
	0: "Clear sky",
	1: "Mainly clear",
	2: "Partly cloudy",
	3: "Overcast",
	45: "Foggy",
	48: "Foggy with rime",
	51: "Light drizzle",
	53: "Drizzle",
	55: "Dense drizzle",
	56: "Light freezing drizzle",
	57: "Dense freezing drizzle",
	61: "Slight rain",
	63: "Rain",
	65: "Heavy rain",
	66: "Light freezing rain",
	67: "Heavy freezing rain",
	71: "Slight snow",
	73: "Snow",
	75: "Heavy snow",
	77: "Snow grains",
	80: "Slight rain showers",
	81: "Rain showers",
	82: "Violent rain showers",
	85: "Slight snow showers",
	86: "Heavy snow showers",
	95: "Thunderstorm",
	96: "Thunderstorm with slight hail",
	99: "Thunderstorm with heavy hail",
};

function compass(degrees: number): string {
	const dirs = [
		"N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
		"S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
	];
	return dirs[Math.round(degrees / 22.5) % 16];
}

async function fetchJson(url: string, headers?: Record<string, string>) {
	const res = await fetch(url, {
		headers,
		signal: AbortSignal.timeout(8000),
	});
	if (!res.ok) throw new Error(`HTTP ${res.status}`);
	return (await res.json()) as Record<string, unknown>;
}

async function getWeather(
	location: string,
	units: "celsius" | "fahrenheit",
): Promise<string> {
	if (!location.trim()) {
		return JSON.stringify({ error: "No place given." });
	}
	const geo = await fetchJson(
		`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(location)}&count=1&language=en&format=json`,
	);
	const results = geo.results as
		| Array<{
				latitude: number;
				longitude: number;
				name: string;
				country?: string;
				admin1?: string;
		  }>
		| undefined;
	if (!results || results.length === 0) {
		return JSON.stringify({ error: `No place found named '${location}'.` });
	}
	const place = results[0];
	const tempUnit = units === "fahrenheit" ? "fahrenheit" : "celsius";
	const windUnit = units === "fahrenheit" ? "mph" : "kmh";
	const forecast = await fetchJson(
		`https://api.open-meteo.com/v1/forecast?latitude=${place.latitude}&longitude=${place.longitude}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m&temperature_unit=${tempUnit}&wind_speed_unit=${windUnit}&timezone=auto`,
	);
	const current = forecast.current as Record<string, unknown>;
	const code = Number(current.weather_code ?? -1);
	return JSON.stringify({
		place: [place.name, place.admin1, place.country]
			.filter(Boolean)
			.join(", "),
		condition: WMO_DESCRIPTIONS[code] ?? "Unknown",
		temperature: current.temperature_2m,
		feels_like: current.apparent_temperature,
		unit: units === "fahrenheit" ? "F" : "C",
		humidity_percent: current.relative_humidity_2m,
		wind_speed: current.wind_speed_10m,
		wind_unit: windUnit,
		wind_direction: compass(Number(current.wind_direction_10m ?? 0)),
		time: current.time,
		timezone: forecast.timezone,
	});
}

// ---------------------------------------------------------------------------
// Web research (Wikipedia, free, no key)
// ---------------------------------------------------------------------------

const WIKI_UA = { "User-Agent": "VESPER/1.0 (https://rankforgetool.com)" };

async function searchWeb(query: string): Promise<string> {
	if (!query.trim()) {
		return JSON.stringify({ error: "No topic given." });
	}
	const search = await fetchJson(
		`https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(query)}&srlimit=3&format=json`,
		WIKI_UA,
	);
	const list = (search.query as Record<string, unknown> | undefined)
		?.search as Array<{ title: string }> | undefined;
	if (!list || list.length === 0) {
		return JSON.stringify({
			error: `No Wikipedia article found for '${query}'.`,
		});
	}
	const title = list[0].title;
	const summary = await fetchJson(
		`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`,
		WIKI_UA,
	);
	return JSON.stringify({
		title: summary.title ?? title,
		summary: summary.extract ?? "",
		url:
			(summary.content_urls as Record<string, Record<string, string>> | undefined)
				?.desktop?.page ??
			`https://en.wikipedia.org/wiki/${encodeURIComponent(title)}`,
	});
}

// ---------------------------------------------------------------------------
// Calculator (small recursive-descent parser; no eval)
// ---------------------------------------------------------------------------

type Token =
	| { kind: "num"; value: number }
	| { kind: "op"; value: string }
	| { kind: "name"; value: string }
	| { kind: "lparen" }
	| { kind: "rparen" };

function tokenize(expr: string): Token[] {
	const tokens: Token[] = [];
	let i = 0;
	while (i < expr.length) {
		const ch = expr[i];
		if (ch === " " || ch === "\t") {
			i++;
			continue;
		}
		if (/[0-9.]/.test(ch)) {
			let num = "";
			while (i < expr.length && /[0-9.]/.test(expr[i])) num += expr[i++];
			const value = Number(num);
			if (!Number.isFinite(value) || num.split(".").length > 2) {
				throw new Error(`Bad number '${num}'`);
			}
			tokens.push({ kind: "num", value });
			continue;
		}
		if (/[a-zA-Z]/.test(ch)) {
			let name = "";
			while (i < expr.length && /[a-zA-Z]/.test(expr[i])) name += expr[i++];
			tokens.push({ kind: "name", value: name.toLowerCase() });
			continue;
		}
		if ("+-*/%^(),".includes(ch)) {
			tokens.push({ kind: "op", value: ch });
			i++;
			continue;
		}
		if (ch === "(") {
			tokens.push({ kind: "lparen" });
			i++;
			continue;
		}
		if (ch === ")") {
			tokens.push({ kind: "rparen" });
			i++;
			continue;
		}
		throw new Error(`Unexpected character '${ch}'`);
	}
	return tokens;
}

function calculate(expression: string): string {
	try {
		if (!expression.trim()) {
			return JSON.stringify({ error: "No expression given." });
		}
		if (expression.length > 200) {
			return JSON.stringify({ error: "Expression too long." });
		}
		const tokens = tokenize(expression);
		let pos = 0;
		const peek = (): Token | undefined => tokens[pos];
		const next = (): Token | undefined => tokens[pos++];

		const parseExpr = (): number => {
			let value = parseTerm();
			for (;;) {
				const t = peek();
				if (t?.kind === "op" && (t.value === "+" || t.value === "-")) {
					next();
					const rhs = parseTerm();
					value = t.value === "+" ? value + rhs : value - rhs;
				} else return value;
			}
		};
		const parseTerm = (): number => {
			let value = parseFactor();
			for (;;) {
				const t = peek();
				if (t?.kind === "op" && (t.value === "*" || t.value === "/" || t.value === "%")) {
					next();
					const rhs = parseFactor();
					if (t.value === "*") value *= rhs;
					else if (t.value === "/") {
						if (rhs === 0) throw new Error("Division by zero");
						value /= rhs;
					} else value %= rhs;
				} else return value;
			}
		};
		const parseFactor = (): number => {
			const base = parseUnary();
			const t = peek();
			if (t?.kind === "op" && t.value === "^") {
				next();
				return Math.pow(base, parseFactor());
			}
			return base;
		};
		const parseUnary = (): number => {
			const t = peek();
			if (t?.kind === "op" && t.value === "-") {
				next();
				return -parseUnary();
			}
			if (t?.kind === "op" && t.value === "+") {
				next();
				return parseUnary();
			}
			return parsePrimary();
		};
		const parsePrimary = (): number => {
			const t = next();
			if (!t) throw new Error("Unexpected end of expression");
			if (t.kind === "num") return t.value;
			if (t.kind === "lparen") {
				const value = parseExpr();
				const close = next();
				if (!close || close.kind !== "rparen") {
					throw new Error("Missing closing bracket");
				}
				return value;
			}
			if (t.kind === "name") {
				if (t.value === "pi") return Math.PI;
				if (t.value === "e") return Math.E;
				const open = next();
				if (!open || open.kind !== "lparen") {
					throw new Error(`Expected '(' after ${t.value}`);
				}
				const arg = parseExpr();
				const close = next();
				if (!close || close.kind !== "rparen") {
					throw new Error("Missing closing bracket");
				}
				switch (t.value) {
					case "sqrt":
						if (arg < 0) throw new Error("Square root of a negative number");
						return Math.sqrt(arg);
					case "abs":
						return Math.abs(arg);
					case "sin":
						return Math.sin(arg);
					case "cos":
						return Math.cos(arg);
					case "tan":
						return Math.tan(arg);
					case "log":
						if (arg <= 0) throw new Error("Log of a non-positive number");
						return Math.log10(arg);
					case "ln":
						if (arg <= 0) throw new Error("Log of a non-positive number");
						return Math.log(arg);
					default:
						throw new Error(`Unknown function '${t.value}'`);
				}
			}
			throw new Error("Unexpected token in expression");
		};

		const value = parseExpr();
		if (pos < tokens.length) {
			throw new Error("Unexpected extra input after the expression");
		}
		if (!Number.isFinite(value)) {
			throw new Error("Result is not a finite number");
		}
		return JSON.stringify({ expression, result: value });
	} catch (error) {
		return JSON.stringify({
			error: error instanceof Error ? error.message : "Could not calculate that.",
		});
	}
}

// ---------------------------------------------------------------------------
// Date and time
// ---------------------------------------------------------------------------

function getDateTime(timezone: string): string {
	try {
		const now = new Date();
		const fmt = new Intl.DateTimeFormat("en-GB", {
			timeZone: timezone,
			weekday: "long",
			year: "numeric",
			month: "long",
			day: "numeric",
			hour: "2-digit",
			minute: "2-digit",
			hour12: false,
		});
		const utcFmt = new Intl.DateTimeFormat("en-GB", {
			timeZone: "UTC",
			weekday: "long",
			year: "numeric",
			month: "long",
			day: "numeric",
			hour: "2-digit",
			minute: "2-digit",
			hour12: false,
		});
		return JSON.stringify({
			timezone,
			local: fmt.format(now),
			utc: utcFmt.format(now),
		});
	} catch {
		return JSON.stringify({
			error: `Unknown timezone '${timezone}'. Try a name like Asia/Riyadh or Europe/London.`,
		});
	}
}
