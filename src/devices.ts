/**
 * VESPER Windows Companion — device endpoints (Next Gen, Milestone 1).
 *
 * The companion app on the user's Windows laptop talks to these
 * endpoints over HTTPS. The laptop is behind a home router, so
 * nothing connects INTO it: the app polls the Worker, and the
 * Worker holds a command queue in D1 (used from Milestone 2).
 *
 * Endpoints:
 * - POST /api/devices/pair      (site key)  register a new device, status 'pending'
 * - POST /api/devices/confirm   (site key)  approve a pending device with its 6-digit code
 * - POST /api/devices/heartbeat (device secret) send CPU/RAM/disk/network status
 * - GET  /api/devices/commands  (device secret) fetch pending commands (empty in M1)
 * - POST /api/devices/result    (device secret) report a command result
 *
 * Pair/confirm use the site key and the normal rate limit, because
 * a person (or the website) calls them. The device endpoints use
 * the device secret instead and are NOT rate-limited by the site
 * limiter: the app heartbeats and polls every few seconds, and a
 * 64-hex-char secret is the real protection there.
 *
 * The device secret itself is never stored: only its SHA-256 hash.
 * The pairing code is single-use: confirming clears it.
 */

import type { Env } from "./types";
import { checkAuth, checkRateLimit } from "./security";

const jsonHeaders = {
	"content-type": "application/json",
	"Access-Control-Allow-Origin": "*",
};

function json(data: unknown, status = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: jsonHeaders,
	});
}

async function sha256Hex(text: string): Promise<string> {
	const data = new TextEncoder().encode(text);
	const digest = await crypto.subtle.digest("SHA-256", data);
	return [...new Uint8Array(digest)]
		.map((b) => b.toString(16).padStart(2, "0"))
		.join("");
}

/** Constant-time string comparison, so secret guesses leak no timing. */
function timingSafeEqual(a: string, b: string): boolean {
	if (a.length !== b.length) return false;
	let diff = 0;
	for (let i = 0; i < a.length; i++) {
		diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
	}
	return diff === 0;
}

type DeviceRow = {
	id: string;
	name: string | null;
	secret_hash: string;
	pair_code: string | null;
	status: string;
	last_seen: number | null;
	last_status: string | null;
	paired_at: number | null;
	created_at: number;
};

/**
 * Checks the X-Vesper-Device header: "<device_id>:<secret>".
 * The secret is hashed and compared with the stored hash.
 * Returns the device row when it checks out.
 */
async function checkDeviceAuth(
	request: Request,
	env: Env,
): Promise<{ ok: boolean; device?: DeviceRow; status?: number; reason?: string }> {
	if (!env.DB) {
		return { ok: false, status: 500, reason: "Database unavailable" };
	}
	const header = request.headers.get("X-Vesper-Device") ?? "";
	const sep = header.indexOf(":");
	if (sep <= 0) {
		return { ok: false, status: 401, reason: "Missing device credentials" };
	}
	const deviceId = header.slice(0, sep);
	const secret = header.slice(sep + 1);
	if (deviceId.length > 64 || secret.length > 128) {
		return { ok: false, status: 401, reason: "Bad device credentials" };
	}
	const row = await env.DB.prepare(
		"SELECT id, name, secret_hash, pair_code, status, last_seen, last_status, paired_at, created_at FROM devices WHERE id = ?1",
	)
		.bind(deviceId)
		.first<DeviceRow>();
	if (!row) {
		return { ok: false, status: 401, reason: "Unknown device" };
	}
	const hash = await sha256Hex(secret);
	if (!timingSafeEqual(hash, row.secret_hash)) {
		return { ok: false, status: 401, reason: "Wrong device secret" };
	}
	if (row.status === "revoked") {
		return { ok: false, status: 403, reason: "Device pairing revoked" };
	}
	return { ok: true, device: row };
}

/**
 * Routes /api/devices/* requests. Returns a JSON response.
 */
export async function handleDevicesRequest(
	request: Request,
	env: Env,
): Promise<Response> {
	const url = new URL(request.url);
	const path = url.pathname;
	try {
		if (path === "/api/devices/pair" && request.method === "POST") {
			return handlePair(request, env);
		}
		if (path === "/api/devices/confirm" && request.method === "POST") {
			return handleConfirm(request, env);
		}
		if (path === "/api/devices/heartbeat" && request.method === "POST") {
			return handleHeartbeat(request, env);
		}
		if (path === "/api/devices/commands" && request.method === "GET") {
			return handleCommands(request, env);
		}
		if (path === "/api/devices/result" && request.method === "POST") {
			return handleResult(request, env);
		}
		return json({ error: "Not found" }, 404);
	} catch (error) {
		console.error("Device request failed:", error);
		return json({ error: "Failed to process device request" }, 500);
	}
}

/**
 * Pairing and confirmation are owner actions: they need the site
 * key and go through the normal rate limit.
 */
async function requireSiteAccess(
	request: Request,
	env: Env,
): Promise<Response | null> {
	const limit = await checkRateLimit(request, env);
	if (!limit.ok) {
		return json(
			{ error: "Too many requests. Please wait a minute and try again." },
			429,
		);
	}
	const auth = await checkAuth(request, env);
	if (!auth.ok) {
		return json({ error: "Unauthorized" }, 401);
	}
	return null;
}

async function readBody(request: Request): Promise<Record<string, unknown>> {
	try {
		const body = (await request.json()) as Record<string, unknown>;
		return body && typeof body === "object" ? body : {};
	} catch {
		return {};
	}
}

/**
 * Register a new device. The app generates its own device id,
 * secret and 6-digit pairing code, and calls this once. The
 * device is stored as 'pending' until the owner confirms the
 * code (handleConfirm). The secret is stored as a hash only.
 */
async function handlePair(request: Request, env: Env): Promise<Response> {
	const blocked = await requireSiteAccess(request, env);
	if (blocked) return blocked;
	if (!env.DB) return json({ error: "Database unavailable" }, 500);

	const body = await readBody(request);
	const deviceId = String(body.device_id ?? "");
	const name = String(body.name ?? "").slice(0, 64);
	const secret = String(body.secret ?? "");
	const code = String(body.code ?? "");
	if (!/^[A-Za-z0-9-]{8,64}$/.test(deviceId)) {
		return json({ error: "device_id must be 8-64 letters, digits or dashes" }, 400);
	}
	if (!/^[0-9a-f]{64}$/.test(secret)) {
		return json({ error: "secret must be 64 hex characters" }, 400);
	}
	if (!/^[0-9]{6}$/.test(code)) {
		return json({ error: "code must be 6 digits" }, 400);
	}
	const existing = await env.DB.prepare(
		"SELECT id, status FROM devices WHERE id = ?1",
	)
		.bind(deviceId)
		.first<{ id: string; status: string }>();
	if (existing) {
		return json(
			{ error: "Device already registered", status: existing.status },
			409,
		);
	}
	const secretHash = await sha256Hex(secret);
	const now = Date.now();
	await env.DB.prepare(
		"INSERT INTO devices (id, name, secret_hash, pair_code, status, created_at) VALUES (?1, ?2, ?3, ?4, 'pending', ?5)",
	)
		.bind(deviceId, name || "Windows laptop", secretHash, code, now)
		.run();
	return json({
		ok: true,
		status: "pending",
		message:
			"Device registered. Give the 6-digit pairing code to VESPER to approve it.",
	});
}

/**
 * Approve a pending device. The owner gives VESPER the 6-digit
 * code shown by the app; this finds the pending device with that
 * code (most recent first) and marks it 'paired'. The code is
 * cleared so it cannot be used again.
 */
async function handleConfirm(request: Request, env: Env): Promise<Response> {
	const blocked = await requireSiteAccess(request, env);
	if (blocked) return blocked;
	if (!env.DB) return json({ error: "Database unavailable" }, 500);

	const body = await readBody(request);
	const code = String(body.code ?? "");
	if (!/^[0-9]{6}$/.test(code)) {
		return json({ error: "code must be 6 digits" }, 400);
	}
	const row = await env.DB.prepare(
		"SELECT id, name FROM devices WHERE pair_code = ?1 AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
	)
		.bind(code)
		.first<{ id: string; name: string | null }>();
	if (!row) {
		return json(
			{ error: "No pending device with that code. Check the code and try again." },
			404,
		);
	}
	const now = Date.now();
	await env.DB.prepare(
		"UPDATE devices SET status = 'paired', pair_code = NULL, paired_at = ?1 WHERE id = ?2",
	)
		.bind(now, row.id)
		.run();
	return json({
		ok: true,
		status: "paired",
		device_id: row.id,
		name: row.name,
		message: "Device paired. The companion app can now send status.",
	});
}

/**
 * Heartbeat: the app sends its status every 30 seconds.
 * Pending devices get {paired:false} and keep waiting; paired
 * devices get their last_seen/last_status updated.
 */
async function handleHeartbeat(request: Request, env: Env): Promise<Response> {
	const auth = await checkDeviceAuth(request, env);
	if (!auth.ok || !auth.device) {
		return json({ error: auth.reason ?? "Unauthorized" }, auth.status ?? 401);
	}
	if (auth.device.status === "pending") {
		return json({
			paired: false,
			message: "Waiting for the owner to approve the pairing code.",
		});
	}
	const body = await readBody(request);
	const statusText = JSON.stringify(body).slice(0, 8192);
	const now = Date.now();
	await env.DB.prepare(
		"UPDATE devices SET last_seen = ?1, last_status = ?2 WHERE id = ?3",
	)
		.bind(now, statusText, auth.device.id)
		.run();
	return json({ paired: true, ok: true });
}

/**
 * Command queue poll. Milestone 1 has no commands yet, so this
 * returns an empty list; Milestones 2+ fill the queue. Commands
 * handed out here are marked 'delivered' so they are not sent
 * twice.
 */
async function handleCommands(request: Request, env: Env): Promise<Response> {
	const auth = await checkDeviceAuth(request, env);
	if (!auth.ok || !auth.device) {
		return json({ error: auth.reason ?? "Unauthorized" }, auth.status ?? 401);
	}
	if (auth.device.status === "pending") {
		return json({ paired: false, commands: [] });
	}
	const rows = await env.DB.prepare(
		"SELECT id, kind, payload FROM device_commands WHERE device_id = ?1 AND status = 'pending' ORDER BY id ASC LIMIT 10",
	)
		.bind(auth.device.id)
		.all<{ id: number; kind: string; payload: string | null }>();
	const commands = rows.results ?? [];
	if (commands.length > 0) {
		const ids = commands.map((c) => c.id);
		await env.DB.prepare(
			`UPDATE device_commands SET status = 'delivered' WHERE id IN (${ids.map(() => "?").join(",")})`,
		)
			.bind(...ids)
			.run();
	}
	return json({
		paired: true,
		commands: commands.map((c) => ({
			id: c.id,
			kind: c.kind,
			payload: c.payload ? JSON.parse(c.payload) : {},
		})),
	});
}

/**
 * Command result: the app reports what a command did. The
 * command is closed and the outcome is logged in device_actions.
 */
async function handleResult(request: Request, env: Env): Promise<Response> {
	const auth = await checkDeviceAuth(request, env);
	if (!auth.ok || !auth.device) {
		return json({ error: auth.reason ?? "Unauthorized" }, auth.status ?? 401);
	}
	const body = await readBody(request);
	const commandId = Number(body.command_id);
	const ok = body.ok === true;
	const result = String(body.result ?? "").slice(0, 4096);
	if (!Number.isFinite(commandId)) {
		return json({ error: "command_id must be a number" }, 400);
	}
	const now = Date.now();
	await env.DB.prepare(
		"UPDATE device_commands SET status = ?1, result = ?2, completed_at = ?3 WHERE id = ?4 AND device_id = ?5",
	)
		.bind(ok ? "done" : "failed", result, now, commandId, auth.device.id)
		.run();
	await env.DB.prepare(
		"INSERT INTO device_actions (device_id, kind, detail, approved, created_at) VALUES (?1, 'command_result', ?2, 1, ?3)",
	)
		.bind(auth.device.id, `command ${commandId}: ${ok ? "done" : "failed"}`, now)
		.run();
	return json({ ok: true });
}

/**
 * Reads the latest status of the most recently seen paired device.
 * Used by the get_device_status model tool. Returns a short JSON
 * string the model turns into a plain-words answer.
 */
export async function getDeviceStatus(env: Env): Promise<string> {
	try {
		if (!env.DB) {
			return JSON.stringify({
				online: false,
				reason: "Device memory is unavailable.",
			});
		}
		const row = await env.DB.prepare(
			"SELECT id, name, last_seen, last_status FROM devices WHERE status = 'paired' ORDER BY last_seen DESC LIMIT 1",
		).first<{
			id: string;
			name: string | null;
			last_seen: number | null;
			last_status: string | null;
		}>();
		if (!row) {
			return JSON.stringify({
				online: false,
				reason:
					"No laptop is paired with VESPER yet. Pair the Windows Companion app first.",
			});
		}
		const now = Date.now();
		const lastSeen = row.last_seen ?? 0;
		const ageSeconds = Math.max(0, Math.round((now - lastSeen) / 1000));
		if (!lastSeen || ageSeconds > 120) {
			return JSON.stringify({
				online: false,
				reason: `The laptop '${row.name ?? row.id}' was last seen ${ageSeconds} seconds ago. It looks off, or the companion app is not running.`,
				last_seen_seconds_ago: ageSeconds,
			});
		}
		let status: Record<string, unknown> = {};
		try {
			status = JSON.parse(row.last_status ?? "{}") as Record<string, unknown>;
		} catch {
			status = {};
		}
		return JSON.stringify({
			online: true,
			device: row.name ?? row.id,
			last_seen_seconds_ago: ageSeconds,
			cpu_percent: status.cpu_percent ?? null,
			ram_used_gb: status.ram_used_gb ?? null,
			ram_total_gb: status.ram_total_gb ?? null,
			disks: status.disks ?? null,
			network: status.network ?? null,
			uptime_hours: status.uptime_hours ?? null,
		});
	} catch (error) {
		return JSON.stringify({
			online: false,
			reason: `Could not read device status: ${error instanceof Error ? error.message : "unknown error"}`,
		});
	}
}
