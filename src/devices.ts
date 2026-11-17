/**
 * ATHENA Windows Companion — device endpoints (Next Gen, Milestone 1).
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
 * - POST /api/devices/revoke    (device secret) unpair the device: status 'revoked',
 * - GET  /api/devices/status    (site key)  latest paired device heartbeat, for the N2 dashboard
 * - GET  /api/devices/activity  (site key)  latest paired device audit rows, for the N2 timeline
 *   pending commands cleared, so the app stops and chat sees no laptop
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
import { checkRateLimit } from "./security";
import {
	auditAuth,
	checkOwnerAuth,
	validateBearerToken,
	type AuthContext,
} from "./auth";

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
		if (path === "/api/devices/revoke" && request.method === "POST") {
			return handleRevoke(request, env);
		}
		if (path === "/api/devices/status" && request.method === "GET") {
			return handleDeviceStatus(request, env);
		}
		if (path === "/api/devices/activity" && request.method === "GET") {
			return handleDeviceActivity(request, env);
		}
		return json({ error: "Not found" }, 404);
	} catch (error) {
		console.error("Device request failed:", error);
		return json({ error: "Failed to process device request" }, 500);
	}
}

/**
 * Pairing and confirmation are owner actions: they need the site
 * key (grace period) or a Bearer token, and go through the normal
 * rate limit. Returns the denial response, or null plus the auth
 * context (whose ownerId scopes the new device row).
 */
async function requireOwnerAccess(
	request: Request,
	env: Env,
): Promise<{ denied: Response | null; auth: AuthContext | null }> {
	const limit = await checkRateLimit(request, env);
	if (!limit.ok) {
		return {
			denied: json(
				{ error: "Too many requests. Please wait a minute and try again." },
				429,
			),
			auth: null,
		};
	}
	const auth = await checkOwnerAuth(request, env);
	if (!auth.ok) {
		return { denied: json({ error: "Unauthorized" }, 401), auth: null };
	}
	return { denied: null, auth };
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
 * GET /api/devices/status — the paired laptop's latest heartbeat,
 * for the N2 Devices dashboard. Site key + rate limit, read-only.
 * Returns exactly what getDeviceStatus() already builds for the
 * model — no new logic, no new data.
 */
async function handleDeviceStatus(
	request: Request,
	env: Env,
): Promise<Response> {
	const { denied, auth } = await requireOwnerAccess(request, env);
	if (denied) return denied;
	const status = await getDeviceStatus(env, auth?.ownerId ?? null);
	return new Response(status, {
		headers: {
			"content-type": "application/json",
			"Access-Control-Allow-Origin": "*",
		},
	});
}

/**
 * GET /api/devices/activity?limit=50 — the latest paired device's
 * audit rows (device_actions), newest first, for the N2 Activity
 * timeline. Site key + rate limit, read-only. Returns the stored
 * rows as-is; the app never writes here.
 */
async function handleDeviceActivity(
	request: Request,
	env: Env,
): Promise<Response> {
	const { denied, auth } = await requireOwnerAccess(request, env);
	if (denied) return denied;
	if (!env.DB) return json({ device: null, count: 0, actions: [] });
	const device = await getPairedDevice(env, auth?.ownerId ?? null);
	if (!device) return json({ device: null, count: 0, actions: [] });
	const wanted = Number(
		new URL(request.url).searchParams.get("limit") ?? 50,
	);
	const limit = Number.isFinite(wanted)
		? Math.min(100, Math.max(1, Math.floor(wanted)))
		: 50;
	const rows = await env.DB.prepare(
		"SELECT kind, detail, approved, created_at FROM device_actions WHERE device_id = ?1 ORDER BY id DESC LIMIT ?2",
	)
		.bind(device.id, limit)
		.all<{
			kind: string;
			detail: string;
			approved: number;
			created_at: number;
		}>();
	const actions = rows.results ?? [];
	return json({
		device: device.name ?? device.id,
		count: actions.length,
		actions,
	});
}

/**
 * Register a new device. The app generates its own device id,
 * secret and 6-digit pairing code, and calls this once. The
 * device is stored as 'pending' until the owner confirms the
 * code (handleConfirm). The secret is stored as a hash only.
 */
async function handlePair(request: Request, env: Env): Promise<Response> {
	const { denied, auth } = await requireOwnerAccess(request, env);
	if (denied) return denied;
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
		"INSERT INTO devices (id, name, secret_hash, pair_code, status, created_at, owner_id) VALUES (?1, ?2, ?3, ?4, 'pending', ?5, ?6)",
	)
		.bind(deviceId, name || "Windows laptop", secretHash, code, now, auth?.ownerId ?? null)
		.run();
	return json({
		ok: true,
		status: "pending",
		message:
			"Device registered. Give the 6-digit pairing code to ATHENA to approve it.",
	});
}

/**
 * Approve a pending device. The owner gives ATHENA the 6-digit
 * code shown by the app; this finds the pending device with that
 * code (most recent first) and marks it 'paired'. The code is
 * cleared so it cannot be used again.
 */
async function handleConfirm(request: Request, env: Env): Promise<Response> {
	const { denied } = await requireOwnerAccess(request, env);
	if (denied) return denied;
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
	// Log the action for history: the command's kind and its
	// outcome, so "what did you do on my laptop?" has a real
	// answer. approved = 1 when the action ran (allowlisted, or
	// the user clicked Allow); 0 when it was refused or failed.
	const cmd = await env.DB.prepare(
		"SELECT kind FROM device_commands WHERE id = ?1 AND device_id = ?2",
	)
		.bind(commandId, auth.device.id)
		.first<{ kind: string }>();
	await env.DB.prepare(
		"INSERT INTO device_actions (device_id, kind, detail, approved, created_at, owner_id) VALUES (?1, ?2, ?3, ?4, ?5, (SELECT owner_id FROM devices WHERE id = ?1))",
	)
		.bind(
			auth.device.id,
			cmd?.kind ?? "command_result",
			result.slice(0, 500),
			ok ? 1 : 0,
			now,
		)
		.run();
	return json({ ok: true });
}

/**
 * Unpairs the device that calls it (device secret auth). The
 * device row becomes 'revoked' — checkDeviceAuth then answers
 * 403 to any later heartbeat, command poll or result, so the app
 * stops entirely — and any commands still queued for it are
 * cleared, so nothing runs after the revoke. The app wipes its
 * own local identity after calling this and shows the pairing
 * screen again on next launch, so re-pairing gives a fresh
 * device id and code.
 */
async function handleRevoke(request: Request, env: Env): Promise<Response> {
	// Device-secret path (the companion app itself): revoke the
	// calling device, as before.
	const auth = await checkDeviceAuth(request, env);
	if (auth.ok && auth.device) {
		const now = Date.now();
		await env.DB!.prepare(
			"UPDATE devices SET status = 'revoked' WHERE id = ?1",
		)
			.bind(auth.device.id)
			.run();
		await env.DB!.prepare(
			"DELETE FROM device_commands WHERE device_id = ?1 AND status IN ('pending', 'delivered')",
		)
			.bind(auth.device.id)
			.run();
		await env.DB!.prepare(
			"INSERT INTO device_actions (device_id, kind, detail, approved, created_at, owner_id) VALUES (?1, 'revoke', 'Pairing revoked from the companion app', 1, ?2, (SELECT owner_id FROM devices WHERE id = ?1))",
		)
			.bind(auth.device.id, now)
			.run();
		return json({ ok: true });
	}
	// N5 M2 owner path: a Bearer token revokes any of the owner's
	// devices. Body may name one device_id; without it, every
	// paired device of the owner is revoked. A device that does
	// not belong to the owner answers 404 (not 403 — it does not
	// confirm the device exists). The site key alone cannot
	// revoke here: revocation needs the owner token (or the
	// device's own secret above).
	const bearer = await validateBearerToken(request, env);
	if (!bearer.ok || !bearer.ownerId) {
		return json({ error: auth.reason ?? "Unauthorized" }, auth.status ?? 401);
	}
	if (!env.DB) return json({ error: "Database unavailable" }, 500);
	const body = await readBody(request);
	const deviceId =
		typeof body.device_id === "string" ? body.device_id : "";
	const now = Date.now();
	if (deviceId) {
		const row = await env.DB.prepare(
			"SELECT id, status FROM devices WHERE id = ?1 AND owner_id = ?2",
		)
			.bind(deviceId, bearer.ownerId)
			.first<{ id: string; status: string }>();
		if (!row) {
			return json({ error: "No such device" }, 404);
		}
		await revokeDeviceRows(env, [row.id], now);
	} else {
		const rows = await env.DB.prepare(
			"SELECT id FROM devices WHERE owner_id = ?1 AND status = 'paired'",
		)
			.bind(bearer.ownerId)
			.all<{ id: string }>();
		await revokeDeviceRows(
			env,
			(rows.results ?? []).map((r) => r.id),
			now,
		);
	}
	await auditAuth(env, bearer.ownerId, "revoke", request, deviceId || "all paired devices");
	return json({ ok: true });
}

/**
 * Shared revoke work: mark devices revoked, clear their queued
 * commands, log one device_actions row each. Used by the device
 * path, the owner path, and the revoke_device model tool.
 */
async function revokeDeviceRows(
	env: Env,
	deviceIds: string[],
	now: number,
): Promise<void> {
	for (const deviceId of deviceIds) {
		await env.DB!.prepare(
			"UPDATE devices SET status = 'revoked' WHERE id = ?1",
		)
			.bind(deviceId)
			.run();
		await env.DB!.prepare(
			"DELETE FROM device_commands WHERE device_id = ?1 AND status IN ('pending', 'delivered')",
		)
			.bind(deviceId)
			.run();
		await env.DB!.prepare(
			"INSERT INTO device_actions (device_id, kind, detail, approved, created_at, owner_id) VALUES (?1, 'revoke', 'Pairing revoked by the owner', 1, ?2, (SELECT owner_id FROM devices WHERE id = ?1))",
		)
			.bind(deviceId, now)
			.run();
	}
}

/**
 * Revokes the owner's paired device(s) for the revoke_device model
 * tool (voice-only; the guard lives in executeTool). Owner-scoped
 * when ownerId is set; legacy unscoped when it is null (no owner
 * yet). Returns a short JSON string for the model.
 */
export async function revokeOwnerDevices(
	env: Env,
	ownerId: string | null,
): Promise<string> {
	try {
		if (!env.DB) {
			return JSON.stringify({ error: "Device memory is unavailable." });
		}
		const now = Date.now();
		const rows = ownerId
			? await env.DB.prepare(
					"SELECT id FROM devices WHERE owner_id = ?1 AND status = 'paired'",
				)
					.bind(ownerId)
					.all<{ id: string }>()
			: await env.DB.prepare(
					"SELECT id FROM devices WHERE status = 'paired'",
				).all<{ id: string }>();
		const ids = (rows.results ?? []).map((r) => r.id);
		if (ids.length === 0) {
			return JSON.stringify({
				revoked: false,
				message: "No laptop is paired with ATHENA, so there is nothing to unpair.",
			});
		}
		await revokeDeviceRows(env, ids, now);
		await auditAuth(env, ownerId, "revoke", null, "voice command");
		return JSON.stringify({
			revoked: true,
			devices_revoked: ids.length,
			message:
				"The laptop has been unpaired. The companion app stops, and pairing again needs a fresh pairing code.",
		});
	} catch (error) {
		return JSON.stringify({
			error: `Could not unpair the laptop: ${error instanceof Error ? error.message : "unknown error"}`,
		});
	}
}

/**
 * Reads the latest status of the most recently seen paired device.
 * Used by the get_device_status model tool. Returns a short JSON
 * string the model turns into a plain-words answer.
 */export async function getDeviceStatus(
	env: Env,
	ownerId?: string | null,
): Promise<string> {
	try {
		if (!env.DB) {
			return JSON.stringify({
				online: false,
				reason: "Device memory is unavailable.",
			});
		}
		const row = ownerId
			? await env.DB.prepare(
					"SELECT id, name, last_seen, last_status FROM devices WHERE status = 'paired' AND owner_id = ?1 ORDER BY last_seen DESC LIMIT 1",
				)
					.bind(ownerId)
					.first<{
						id: string;
						name: string | null;
						last_seen: number | null;
						last_status: string | null;
					}>()
			: await env.DB.prepare(
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
					"No laptop is paired with ATHENA yet. Pair the Windows Companion app first.",
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

/**
 * What ATHENA has done on the paired laptop lately, newest
 * first. Reads the device_actions log the companion app writes
 * every time a command finishes (or is refused). No queue, no
 * wait — a direct read, so the user can ask "what did you do
 * on my laptop?" and get a straight answer.
 */
export async function getActionHistory(
	env: Env,
	ownerId?: string | null,
): Promise<string> {
	try {
		if (!env.DB) {
			return JSON.stringify({ error: "Device memory is unavailable." });
		}
		const device = await getPairedDevice(env, ownerId);
		if (!device) {
			return JSON.stringify({
				error:
					"No laptop is paired with ATHENA yet. Pair the Windows Companion app first.",
			});
		}
		const rows = await env.DB.prepare(
			"SELECT kind, detail, approved, created_at FROM device_actions WHERE device_id = ?1 ORDER BY id DESC LIMIT 15",
		)
			.bind(device.id)
			.all<{
				kind: string;
				detail: string;
				approved: number;
				created_at: number;
			}>();
		// Old rows (before Milestone 4) are logged as kind
		// 'command_result' with detail 'command 16: done' — resolve
		// them to the real command kind and its result, so the
		// history reads properly for older actions too.
		const resolved = new Map<number, { kind: string; result: string }>();
		const cmdIds = (rows.results ?? [])
			.filter((r) => r.kind === "command_result")
			.map((r) => Number((r.detail.match(/command (\d+)/) ?? [])[1]))
			.filter((n) => Number.isFinite(n));
		if (cmdIds.length > 0) {
			const placeholders = cmdIds.map(() => "?").join(",");
			const cmds = await env.DB.prepare(
				`SELECT id, kind, result FROM device_commands WHERE id IN (${placeholders})`,
			)
				.bind(...cmdIds)
				.all<{ id: number; kind: string; result: string | null }>();
			for (const c of cmds.results ?? []) {
				resolved.set(c.id, {
					kind: c.kind,
					result: c.result ?? "",
				});
			}
		}
		const now = Date.now();
		const actions = (rows.results ?? []).map((row) => {
			const ageSeconds = Math.max(0, Math.round((now - row.created_at) / 1000));
			const when =
				ageSeconds < 60
					? `${ageSeconds} seconds ago`
					: ageSeconds < 3600
						? `${Math.round(ageSeconds / 60)} minutes ago`
						: ageSeconds < 86400
							? `${Math.round(ageSeconds / 3600)} hours ago`
							: `${Math.round(ageSeconds / 86400)} days ago`;
			let kind = row.kind;
			let detail = row.detail;
			if (kind === "command_result") {
				const id = Number((row.detail.match(/command (\d+)/) ?? [])[1]);
				const cmd = resolved.get(id);
				if (cmd) {
					kind = cmd.kind;
					detail = cmd.result || row.detail;
				}
			}
			return {
				when,
				kind,
				detail,
				allowed: row.approved === 1,
			};
		});
		return JSON.stringify({ actions });
	} catch (error) {
		return JSON.stringify({
			error: `Could not read the history: ${error instanceof Error ? error.message : "unknown error"}`,
		});
	}
}

// ---------------------------------------------------------------------------
// Command queue (Milestone 2) — run_device_command model tool
// ---------------------------------------------------------------------------

/** The most recently seen paired device, if there is one. */
async function getPairedDevice(
	env: Env,
	ownerId?: string | null,
): Promise<{ id: string; name: string | null } | null> {
	if (!env.DB) return null;
	const row = ownerId
		? await env.DB.prepare(
				"SELECT id, name FROM devices WHERE status = 'paired' AND owner_id = ?1 ORDER BY last_seen DESC LIMIT 1",
			)
				.bind(ownerId)
				.first<{ id: string; name: string | null }>()
		: await env.DB.prepare(
				"SELECT id, name FROM devices WHERE status = 'paired' ORDER BY last_seen DESC LIMIT 1",
			).first<{ id: string; name: string | null }>();
	return row ?? null;
}

/**
 * Runs a command on the paired laptop, from the model.
 *
 * The command is put in the D1 queue; the companion app polls the
 * queue every few seconds, runs it if it is allowed, and reports
 * the result back. This function waits for that result, but only
 * up to ~12 seconds: a chat request must finish well inside the
 * Worker's request time limit. If the laptop does not answer in
 * time, the model is told to say so and the user can ask again.
 * The command stays in the queue either way — the app still runs
 * it when it polls, and the result is logged in device_actions.
 *
 * `action` is "open_app" or "search_files" (one tool per action,
 * so the model picks the right one).
 */
export async function runDeviceCommand(
	env: Env,
	action: string,
	args: Record<string, unknown>,
	ownerId?: string | null,
): Promise<string> {
	try {
		if (!env.DB) {
			return JSON.stringify({ error: "Device memory is unavailable." });
		}
		const device = await getPairedDevice(env, ownerId);
		if (!device) {
			return JSON.stringify({
				error:
					"No laptop is paired with ATHENA yet. Pair the Windows Companion app first.",
			});
		}
		// Stale check: a laptop that has not been heard from in two
		// minutes is probably off or the app is closed.
		const seen = await env.DB.prepare(
			"SELECT last_seen FROM devices WHERE id = ?1",
		)
			.bind(device.id)
			.first<{ last_seen: number | null }>();
		const ageSeconds = seen?.last_seen
			? Math.max(0, Math.round((Date.now() - seen.last_seen) / 1000))
			: 99999;
		if (ageSeconds > 120) {
			return JSON.stringify({
				error: `The laptop '${device.name ?? device.id}' was last seen ${ageSeconds} seconds ago. It looks off, or the companion app is not running. Ask the user to open the companion app on the laptop and try again.`,
			});
		}
		const allowedActions = [
			"open_app",
			"search_files",
			"create_file",
			"edit_file",
			"read_file",
			"run_command",
		];
		if (!allowedActions.includes(action)) {
			return JSON.stringify({ error: `Unknown action: ${action}.` });
		}
		const payload: Record<string, unknown> = { action };
		if (action === "open_app") {
			const app = String(args.app ?? "").trim();
			if (!app) {
				return JSON.stringify({ error: "No app name given." });
			}
			payload.app = app;
		} else if (action === "search_files") {
			const folder = String(args.folder ?? "").trim();
			const query = String(args.query ?? "").trim();
			if (!folder || !query) {
				return JSON.stringify({
					error: "A search needs both a folder and something to look for.",
				});
			}
			payload.folder = folder;
			payload.query = query;
		} else if (action === "create_file") {
			const path = String(args.path ?? "").trim();
			const content = String(args.content ?? "");
			if (!path) {
				return JSON.stringify({ error: "No file path given." });
			}
			if (content.length > 20000) {
				return JSON.stringify({
					error: "That file is too long to write in one go (over 20,000 characters). Ask for a shorter file, or split it into parts.",
				});
			}
			payload.path = path;
			payload.content = content;
		} else if (action === "edit_file") {
			const path = String(args.path ?? "").trim();
			const mode = String(args.mode ?? "append").trim();
			const oldText = String(args.old_text ?? "");
			const newText = String(args.new_text ?? "");
			if (!path || !newText) {
				return JSON.stringify({
					error: "An edit needs a file path and the new text.",
				});
			}
			if (mode !== "replace" && mode !== "append") {
				return JSON.stringify({
					error: "An edit is either replace or append.",
				});
			}
			if (mode === "replace" && !oldText) {
				return JSON.stringify({
					error: "A replace edit needs the text to replace.",
				});
			}
			if (newText.length > 20000) {
				return JSON.stringify({
					error: "That text is too long to write in one go (over 20,000 characters). Ask for a shorter edit, or split it into parts.",
				});
			}
			payload.path = path;
			payload.mode = mode;
			payload.old_text = oldText;
			payload.new_text = newText;
		} else if (action === "read_file") {
			const path = String(args.path ?? "").trim();
			if (!path) {
				return JSON.stringify({ error: "No file path given." });
			}
			payload.path = path;
		} else {
			// run_command
			const command = String(args.command ?? "").trim();
			const arg = String(args.arg ?? "").trim();
			if (!command) {
				return JSON.stringify({ error: "No command name given." });
			}
			if (arg.length > 200) {
				return JSON.stringify({
					error: "That argument is too long.",
				});
			}
			payload.command = command;
			payload.arg = arg;
		}
		const now = Date.now();
		// If the same command is already queued or waiting for this
		// device (the user asked twice while the first run was still
		// going), wait on that one instead of queueing a duplicate.
		const payloadJson = JSON.stringify(payload);
		const existing = await env.DB.prepare(
			"SELECT id FROM device_commands WHERE device_id = ?1 AND kind = ?2 AND payload = ?3 AND status IN ('pending', 'delivered') ORDER BY id DESC LIMIT 1",
		)
			.bind(device.id, action, payloadJson)
			.first<{ id: number }>();
		let commandId: number;
		if (existing) {
			commandId = existing.id;
		} else {
			const inserted = await env.DB.prepare(
				"INSERT INTO device_commands (device_id, kind, payload, status, created_at, owner_id) VALUES (?1, ?2, ?3, 'pending', ?4, ?5) RETURNING id",
			)
				.bind(device.id, action, payloadJson, now, ownerId ?? null)
				.first<{ id: number }>();
			if (!inserted) {
				return JSON.stringify({ error: "Could not queue the command." });
			}
			commandId = inserted.id;
		}
		// Wait for the app to run it and report back.
		const deadline = Date.now() + 12_000;
		for (;;) {
			const row = await env.DB.prepare(
				"SELECT status, result FROM device_commands WHERE id = ?1",
			)
				.bind(commandId)
				.first<{ status: string; result: string | null }>();
			if (row && (row.status === "done" || row.status === "failed")) {
				let parsed: unknown = row.result ?? "";
				try {
					parsed = JSON.parse(row.result ?? "");
				} catch {
					parsed = row.result ?? "";
				}
				return JSON.stringify({
					action,
					ok: row.status === "done",
					result: parsed,
				});
			}
			if (Date.now() >= deadline) {
				const isWrite =
					action === "create_file" || action === "edit_file";
				return JSON.stringify({
					action,
					ok: false,
					waiting: true,
					message: isWrite
						? "The command was sent to the laptop and a confirmation popup is waiting on its screen. Tell the user to click Allow on the popup, then ask me again and I will check whether it went through."
						: "The command was sent to the laptop, but it has not answered yet. Tell the user to check the companion app is running and ask again in a moment.",
				});
			}
			await new Promise((resolve) => setTimeout(resolve, 2000));
		}
	} catch (error) {
		return JSON.stringify({
			error: `Command failed: ${error instanceof Error ? error.message : "unknown error"}`,
		});
	}
}
