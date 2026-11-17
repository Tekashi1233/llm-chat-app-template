/**
 * ATHENA N5 M2 — owner auth: password login + session tokens.
 *
 * One owner (id 'owner-1', see migrations/0003_owner_auth.sql).
 * Password: PBKDF2 via WebCrypto, 210,000 iterations, SHA-256,
 * per-owner random 16-byte salt, stored as
 * "pbkdf2$210000$<salt-b64>$<hash-b64>". Nothing password-equivalent
 * ever leaves the server.
 *
 * Sessions: 256-bit random token, only its SHA-256 hash stored,
 * 30-day sliding expiry (refreshed on each validated use).
 * Clients send `Authorization: Bearer <token>`.
 *
 * Grace period: the site key (X-Vesper-Key) still works everywhere
 * it did before. When an owner exists, a site-key request is scoped
 * to that single owner, exactly like a Bearer request. Retiring the
 * site key is M2.5, later — nothing here removes site-key acceptance.
 *
 * Login is rate-limited: 5 attempts/minute per IP, then a short
 * lockout (429, Retry-After 60).
 *
 * Audit: login_success, login_failed, logout, revoke, setup rows in
 * `auth_audit`. Best-effort — an audit failure never breaks auth.
 */

import type { Env } from "./types";
import { checkAuth } from "./security";

/** The single owner id. See migrations/0003_owner_auth.sql. */
export const OWNER_ID = "owner-1";

/** Minimum password length for setup / first-run. */
export const MIN_PASSWORD_LENGTH = 8;

/** Session lifetime: 30 days, sliding on each validated use. */
export const TOKEN_TTL_MS = 30 * 24 * 60 * 60 * 1000;

const PBKDF2_ITERATIONS = 210_000;
const SALT_BYTES = 16;
const HASH_BYTES = 32;

/** Login attempts allowed per IP per minute before lockout. */
const LOGIN_MINUTE_LIMIT = 5;

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

/** Constant-time string comparison, so guesses leak no timing. */
function timingSafeEqual(a: string, b: string): boolean {
	if (a.length !== b.length) return false;
	let diff = 0;
	for (let i = 0; i < a.length; i++) {
		diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
	}
	return diff === 0;
}

function bytesToBase64(bytes: Uint8Array): string {
	let binary = "";
	for (const b of bytes) binary += String.fromCharCode(b);
	return btoa(binary);
}

function base64ToBytes(b64: string): Uint8Array {
	const binary = atob(b64);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	return bytes;
}

function bytesToHex(bytes: Uint8Array): string {
	return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(text: string): Promise<string> {
	const digest = await crypto.subtle.digest(
		"SHA-256",
		new TextEncoder().encode(text),
	);
	return bytesToHex(new Uint8Array(digest));
}

/**
 * Hashes a password with PBKDF2 (210,000 iterations, SHA-256,
 * random 16-byte salt). Returns the stored form
 * "pbkdf2$210000$<salt-b64>$<hash-b64>".
 */
export async function hashPassword(password: string): Promise<string> {
	const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
	const key = await crypto.subtle.importKey(
		"raw",
		new TextEncoder().encode(password),
		"PBKDF2",
		false,
		["deriveBits"],
	);
	const bits = await crypto.subtle.deriveBits(
		{
			name: "PBKDF2",
			salt: salt as BufferSource,
			iterations: PBKDF2_ITERATIONS,
			hash: "SHA-256",
		},
		key,
		HASH_BYTES * 8,
	);
	return [
		"pbkdf2",
		String(PBKDF2_ITERATIONS),
		bytesToBase64(salt),
		bytesToBase64(new Uint8Array(bits)),
	].join("$");
}

/**
 * Verifies a password against a stored hash. False on any malformed
 * stored value — a bad row never authenticates.
 */
export async function verifyPassword(
	password: string,
	stored: string,
): Promise<boolean> {
	try {
		const parts = stored.split("$");
		if (parts.length !== 4 || parts[0] !== "pbkdf2") return false;
		const iterations = Number(parts[1]);
		if (!Number.isInteger(iterations) || iterations < 1) return false;
		const salt = base64ToBytes(parts[2]);
		const expected = parts[3];
		const key = await crypto.subtle.importKey(
			"raw",
			new TextEncoder().encode(password),
			"PBKDF2",
			false,
			["deriveBits"],
		);
		const bits = await crypto.subtle.deriveBits(
			{
				name: "PBKDF2",
				salt: salt as BufferSource,
				iterations,
				hash: "SHA-256",
			},
			key,
			base64ToBytes(expected).length * 8,
		);
		return timingSafeEqual(bytesToBase64(new Uint8Array(bits)), expected);
	} catch {
		return false;
	}
}

/** Creates a fresh 256-bit session token (base64url, returned once). */
export function createToken(): string {
	const bytes = crypto.getRandomValues(new Uint8Array(32));
	return bytesToBase64(bytes)
		.replace(/\+/g, "-")
		.replace(/\//g, "_")
		.replace(/=+$/, "");
}

type OwnerRow = { id: string; password_hash: string };

/** Returns the owner row, or null when no owner exists yet. */
export async function getOwner(env: Env): Promise<OwnerRow | null> {
	if (!env.DB) return null;
	try {
		const row = await env.DB.prepare(
			"SELECT id, password_hash FROM owners LIMIT 1",
		).first<OwnerRow>();
		return row ?? null;
	} catch {
		return null;
	}
}

/** Best-effort audit row. Never throws. */
export async function auditAuth(
	env: Env,
	ownerId: string | null,
	event: string,
	request?: Request | null,
	detail?: string,
): Promise<void> {
	try {
		if (!env.DB) return;
		const ip = request?.headers.get("CF-Connecting-IP") || "unknown";
		await env.DB.prepare(
			"INSERT INTO auth_audit (owner_id, event, ip, created_at, detail) VALUES (?1, ?2, ?3, ?4, ?5)",
		)
			.bind(ownerId, event, ip.slice(0, 64), Date.now(), (detail ?? "").slice(0, 500))
			.run();
	} catch (error) {
		console.error("Failed to write auth audit row:", error);
	}
}

type SessionRow = {
	token_hash: string;
	owner_id: string;
	device_label: string | null;
	created_at: number;
	expires_at: number;
	last_seen_at: number;
	revoked: number;
};

/**
 * Validates a Bearer token: looks up its SHA-256 hash, rejects
 * revoked or expired sessions, and slides the expiry (30 days
 * from now) plus last_seen on each validated use.
 */
export async function validateBearerToken(
	request: Request,
	env: Env,
): Promise<{ ok: boolean; ownerId?: string; tokenHash?: string }> {
	const header = request.headers.get("Authorization") ?? "";
	const match = /^Bearer\s+(\S+)$/.exec(header);
	if (!match) return { ok: false };
	if (!env.DB) return { ok: false };
	const tokenHash = await sha256Hex(match[1]);
	const row = await env.DB.prepare(
		"SELECT token_hash, owner_id, expires_at, revoked FROM sessions WHERE token_hash = ?1",
	)
		.bind(tokenHash)
		.first<SessionRow>();
	if (!row) return { ok: false };
	if (row.revoked === 1) return { ok: false };
	if (row.expires_at <= Date.now()) return { ok: false };
	const now = Date.now();
	await env.DB.prepare(
		"UPDATE sessions SET last_seen_at = ?1, expires_at = ?2 WHERE token_hash = ?3",
	)
		.bind(now, now + TOKEN_TTL_MS, tokenHash)
		.run();
	return { ok: true, ownerId: row.owner_id, tokenHash };
}

/**
 * The auth result every protected endpoint uses.
 * - via 'bearer': owner-scoped to the token's owner.
 * - via 'site-key': grace period. When an owner exists, scoped to
 *   that single owner; before any owner exists, ownerId is null
 *   and callers keep the legacy unscoped behaviour.
 */
export type AuthContext = {
	ok: boolean;
	ownerId: string | null;
	via: "bearer" | "site-key" | "none";
	reason?: string;
};

/**
 * Accepts EITHER a Bearer token OR the site key. A Bearer header
 * that is present but invalid fails closed — it does not fall
 * through to the site key, so a stolen/expired token cannot be
 * bypassed by also sending the (public) site key.
 */
export async function checkOwnerAuth(
	request: Request,
	env: Env,
): Promise<AuthContext> {
	const authHeader = request.headers.get("Authorization") ?? "";
	if (authHeader.trim().length > 0) {
		const bearer = await validateBearerToken(request, env);
		if (!bearer.ok) {
			return { ok: false, ownerId: null, via: "none", reason: "Invalid or expired token" };
		}
		return { ok: true, ownerId: bearer.ownerId ?? null, via: "bearer" };
	}
	const site = await checkAuth(request, env);
	if (!site.ok) {
		return { ok: false, ownerId: null, via: "none", reason: site.reason };
	}
	const owner = await getOwner(env);
	return { ok: true, ownerId: owner?.id ?? null, via: "site-key" };
}

/**
 * Creates a session row for an owner and returns the token (once)
 * plus its expiry. The token itself is never stored — only its
 * SHA-256 hash.
 */
export async function createSession(
	env: Env,
	ownerId: string,
	deviceLabel: string | null,
): Promise<{ token: string; expires_at: number }> {
	const token = createToken();
	const tokenHash = await sha256Hex(token);
	const now = Date.now();
	await env.DB.prepare(
		"INSERT INTO sessions (token_hash, owner_id, device_label, created_at, expires_at, last_seen_at, revoked) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 0)",
	)
		.bind(tokenHash, ownerId, deviceLabel, now, now + TOKEN_TTL_MS, now)
		.run();
	return { token, expires_at: now + TOKEN_TTL_MS };
}

/**
 * Login rate limit: 5 attempts/minute per IP (stored in the same
 * `rate_limits` table as the chat limiter, under a `login:` key).
 * Fail-open on store errors, like the chat limiter.
 */
export async function checkLoginRateLimit(
	request: Request,
	env: Env,
): Promise<{ ok: boolean; retryAfter?: number }> {
	try {
		if (!env.DB) return { ok: true };
		const ip = request.headers.get("CF-Connecting-IP") || "unknown";
		const now = Date.now();
		const minuteBucket = Math.floor(now / 60000);
		const key = `login:${ip}:${minuteBucket}`;
		const row = await env.DB.prepare(
			"INSERT INTO rate_limits (key, count, updated_at) VALUES (?1, 1, ?2) " +
				"ON CONFLICT(key) DO UPDATE SET count = count + 1, updated_at = ?2 " +
				"RETURNING count",
		)
			.bind(key, now)
			.first<{ count: number }>();
		const count = row?.count ?? 0;
		if (count > LOGIN_MINUTE_LIMIT) {
			return { ok: false, retryAfter: 60 };
		}
		return { ok: true };
	} catch (error) {
		console.error("Login rate limit check failed (allowing request):", error);
		return { ok: true };
	}
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
 * Routes /api/auth/* requests. Returns a JSON response.
 */
export async function handleAuthRequest(
	request: Request,
	env: Env,
): Promise<Response> {
	const url = new URL(request.url);
	const path = url.pathname;
	try {
		if (path === "/api/auth/status" && request.method === "GET") {
			const owner = await getOwner(env);
			return json({ setup_required: owner === null });
		}
		if (path === "/api/auth/setup" && request.method === "POST") {
			return handleSetup(request, env);
		}
		if (path === "/api/auth/login" && request.method === "POST") {
			return handleLogin(request, env);
		}
		if (path === "/api/auth/logout" && request.method === "POST") {
			return handleLogout(request, env);
		}
		if (path === "/api/auth/logout-all" && request.method === "POST") {
			return handleLogoutAll(request, env);
		}
		if (path === "/api/auth/sessions" && request.method === "GET") {
			return handleListSessions(request, env);
		}
		if (path === "/api/auth/exchange" && request.method === "POST") {
			return handleExchange(request, env);
		}
		return json({ error: "Not found" }, 404);
	} catch (error) {
		console.error("Auth request failed:", error);
		return json({ error: "Failed to process auth request" }, 500);
	}
}

/**
 * POST /api/auth/setup — first-run only, site-key auth. The owner
 * sets the password once; a session token is returned so the
 * client is logged in immediately. 409 when an owner already
 * exists; 400 when the password is shorter than 8 characters.
 */
async function handleSetup(request: Request, env: Env): Promise<Response> {
	const site = await checkAuth(request, env);
	if (!site.ok) return json({ error: "Unauthorized" }, 401);
	if (!env.DB) return json({ error: "Database unavailable" }, 500);
	const existing = await getOwner(env);
	if (existing) {
		return json({ error: "An owner account already exists" }, 409);
	}
	const body = await readBody(request);
	const password = String(body.password ?? "");
	if (password.length < MIN_PASSWORD_LENGTH) {
		return json(
			{ error: `Password must be at least ${MIN_PASSWORD_LENGTH} characters` },
			400,
		);
	}
	const deviceLabel =
		typeof body.device_label === "string"
			? body.device_label.slice(0, 64)
			: null;
	const passwordHash = await hashPassword(password);
	const now = Date.now();
	await env.DB.prepare(
		"INSERT INTO owners (id, password_hash, created_at) VALUES (?1, ?2, ?3)",
	)
		.bind(OWNER_ID, passwordHash, now)
		.run();
	const session = await createSession(env, OWNER_ID, deviceLabel);
	await auditAuth(env, OWNER_ID, "setup", request, deviceLabel ?? undefined);
	return json({ token: session.token, expires_at: session.expires_at });
}

/**
 * POST /api/auth/login — password login. 401 on a wrong password,
 * 404 with setup_required when no owner exists yet. Rate-limited
 * to 5 attempts/minute per IP, then a short lockout (429).
 */
async function handleLogin(request: Request, env: Env): Promise<Response> {
	const limit = await checkLoginRateLimit(request, env);
	if (!limit.ok) {
		return json(
			{ error: "Too many login attempts. Please wait a minute and try again." },
			429,
		);
	}
	if (!env.DB) return json({ error: "Database unavailable" }, 500);
	const owner = await getOwner(env);
	if (!owner) {
		return json(
			{ error: "No owner account yet", setup_required: true },
			404,
		);
	}
	const body = await readBody(request);
	const password = String(body.password ?? "");
	const deviceLabel =
		typeof body.device_label === "string"
			? body.device_label.slice(0, 64)
			: null;
	const ok = await verifyPassword(password, owner.password_hash);
	if (!ok) {
		await auditAuth(env, owner.id, "login_failed", request);
		return json({ error: "Wrong password" }, 401);
	}
	const session = await createSession(env, owner.id, deviceLabel);
	await auditAuth(env, owner.id, "login_success", request, deviceLabel ?? undefined);
	return json({ token: session.token, expires_at: session.expires_at });
}

/** POST /api/auth/logout — Bearer, revokes just that session. */
async function handleLogout(request: Request, env: Env): Promise<Response> {
	const bearer = await validateBearerToken(request, env);
	if (!bearer.ok || !bearer.tokenHash) {
		return json({ error: "Unauthorized" }, 401);
	}
	if (!env.DB) return json({ error: "Database unavailable" }, 500);
	await env.DB.prepare(
		"UPDATE sessions SET revoked = 1 WHERE token_hash = ?1",
	)
		.bind(bearer.tokenHash)
		.run();
	await auditAuth(env, bearer.ownerId ?? null, "logout", request);
	return json({ ok: true });
}

/** POST /api/auth/logout-all — Bearer, revokes all owner sessions. */
async function handleLogoutAll(request: Request, env: Env): Promise<Response> {
	const bearer = await validateBearerToken(request, env);
	if (!bearer.ok || !bearer.tokenHash) {
		return json({ error: "Unauthorized" }, 401);
	}
	if (!env.DB) return json({ error: "Database unavailable" }, 500);
	await env.DB.prepare(
		"UPDATE sessions SET revoked = 1 WHERE owner_id = ?1 AND revoked = 0",
	)
		.bind(bearer.ownerId)
		.run();
	await auditAuth(env, bearer.ownerId ?? null, "logout", request, "all sessions");
	return json({ ok: true });
}

/**
 * GET /api/auth/sessions — Bearer, lists the owner's sessions
 * (device label, created, last seen, expiry, revoked). The token
 * hash is never returned — only its first 8 characters as an id,
 * so the owner can tell sessions apart.
 */
async function handleListSessions(
	request: Request,
	env: Env,
): Promise<Response> {
	const bearer = await validateBearerToken(request, env);
	if (!bearer.ok) {
		return json({ error: "Unauthorized" }, 401);
	}
	if (!env.DB) return json({ error: "Database unavailable" }, 500);
	const rows = await env.DB.prepare(
		"SELECT token_hash, device_label, created_at, expires_at, last_seen_at, revoked FROM sessions WHERE owner_id = ?1 ORDER BY created_at DESC LIMIT 50",
	)
		.bind(bearer.ownerId)
		.all<SessionRow>();
	const sessions = (rows.results ?? []).map((row) => ({
		id: row.token_hash.slice(0, 8),
		device_label: row.device_label,
		created_at: row.created_at,
		expires_at: row.expires_at,
		last_seen_at: row.last_seen_at,
		revoked: row.revoked === 1,
	}));
	return json({ count: sessions.length, sessions });
}

/**
 * POST /api/auth/exchange — site-key auth. Existing installs swap
 * their stored site key for a session token once, then delete the
 * key from their local config. 409 when no owner exists yet (the
 * owner must run setup first).
 */
async function handleExchange(request: Request, env: Env): Promise<Response> {
	const site = await checkAuth(request, env);
	if (!site.ok) return json({ error: "Unauthorized" }, 401);
	if (!env.DB) return json({ error: "Database unavailable" }, 500);
	const owner = await getOwner(env);
	if (!owner) {
		return json(
			{ error: "No owner account yet", setup_required: true },
			409,
		);
	}
	const body = await readBody(request);
	const deviceLabel =
		typeof body.device_label === "string"
			? body.device_label.slice(0, 64)
			: null;
	const session = await createSession(env, owner.id, deviceLabel);
	await auditAuth(env, owner.id, "login_success", request, "exchange");
	return json({ token: session.token, expires_at: session.expires_at });
}
