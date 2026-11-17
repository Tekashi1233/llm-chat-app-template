/**
 * ATHENA Phase 7 — protection: API key auth + rate limiting.
 *
 * The API key lives in D1 (table `settings`, key 'api_key'), NOT in
 * the repo — the repo is public, so a key in wrangler.jsonc or in
 * source would be visible to everyone. It is set with the D1 API
 * and cached in memory for 5 minutes per Worker isolate.
 *
 * Auth is fail-closed: if the key cannot be verified, the request
 * is refused. Rate limiting is fail-open: if its store fails, the
 * request is allowed (limiting must never break chat by itself).
 *
 * Honest limit: the key is sent by the public website, so anyone
 * who reads the page source can find it. This stops drive-by abuse
 * of the free quota, not a determined attacker. The per-IP rate
 * limits still apply even if the key leaks.
 */

import type { Env } from "./types";

const KEY_CACHE_TTL_MS = 5 * 60 * 1000;
const MINUTE_LIMIT = 20;
const DAY_LIMIT = 300;
const GLOBAL_DAY_LIMIT = 500;

let cachedKey: { value: string; fetchedAt: number } | null = null;

/**
 * Loads the API key from D1, with a short in-memory cache.
 * Returns null when no key is configured or D1 cannot be read.
 */
export async function getApiKey(env: Env): Promise<string | null> {
	const now = Date.now();
	if (cachedKey && now - cachedKey.fetchedAt < KEY_CACHE_TTL_MS) {
		return cachedKey.value;
	}
	try {
		if (!env.DB) return cachedKey?.value ?? null;
		const row = await env.DB.prepare(
			"SELECT value FROM settings WHERE key = 'api_key'",
		).first<{ value: string }>();
		if (!row?.value) return cachedKey?.value ?? null;
		cachedKey = { value: row.value, fetchedAt: now };
		return row.value;
	} catch (error) {
		console.error("Failed to load API key:", error);
		return cachedKey?.value ?? null;
	}
}

/** Constant-time string comparison, so key guesses leak no timing. */
function timingSafeEqual(a: string, b: string): boolean {
	if (a.length !== b.length) return false;
	let diff = 0;
	for (let i = 0; i < a.length; i++) {
		diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
	}
	return diff === 0;
}

/**
 * Checks the X-Vesper-Key header against the key in D1.
 * Fail-closed: any problem verifying means refused.
 */
export async function checkAuth(
	request: Request,
	env: Env,
): Promise<{ ok: boolean; reason?: string }> {
	const key = await getApiKey(env);
	if (!key) {
		return { ok: false, reason: "API key not configured on the server" };
	}
	const supplied = request.headers.get("X-Vesper-Key") ?? "";
	if (supplied.length === 0) {
		return { ok: false, reason: "Missing X-Vesper-Key header" };
	}
	if (!timingSafeEqual(supplied, key)) {
		return { ok: false, reason: "Wrong API key" };
	}
	return { ok: true };
}

/**
 * Per-IP and global rate limits, stored in D1 table `rate_limits`:
 * 20 requests/minute per IP, 300/day per IP, 500/day total.
 * Fail-open: on any store error the request is allowed.
 */
export async function checkRateLimit(
	request: Request,
	env: Env,
): Promise<{ ok: boolean; retryAfter?: number }> {
	try {
		if (!env.DB) return { ok: true };
		const ip = request.headers.get("CF-Connecting-IP") || "unknown";
		const now = Date.now();
		const minuteBucket = Math.floor(now / 60000);
		const day = new Date(now).toISOString().slice(0, 10);
		const keys = [
			`min:${ip}:${minuteBucket}`,
			`day:${ip}:${day}`,
			`global:${day}`,
		];
		const stmts = keys.map((k) =>
			env.DB.prepare(
				"INSERT INTO rate_limits (key, count, updated_at) VALUES (?1, 1, ?2) " +
					"ON CONFLICT(key) DO UPDATE SET count = count + 1, updated_at = ?2 " +
					"RETURNING count",
			).bind(k, now),
		);
		// Best-effort cleanup of counters older than 48 hours
		stmts.push(
			env.DB.prepare("DELETE FROM rate_limits WHERE updated_at < ?1").bind(
				now - 48 * 3600 * 1000,
			),
		);
		const results = await env.DB.batch(stmts);
		const counts = results
			.slice(0, 3)
			.map(
				(r) =>
					(r.results?.[0] as { count: number } | undefined)?.count ?? 0,
			);
		const [perMinute, perDay, globalDay] = counts;
		if (
			perMinute > MINUTE_LIMIT ||
			perDay > DAY_LIMIT ||
			globalDay > GLOBAL_DAY_LIMIT
		) {
			return { ok: false, retryAfter: 60 };
		}
		return { ok: true };
	} catch (error) {
		console.error("Rate limit check failed (allowing request):", error);
		return { ok: true };
	}
}

/** The limits above, for /api/model-info to report. */
export const RATE_LIMITS = {
	per_ip_per_minute: MINUTE_LIMIT,
	per_ip_per_day: DAY_LIMIT,
	global_per_day: GLOBAL_DAY_LIMIT,
};
