using System.Net.Http.Json;

namespace Athena.Core;

/// <summary>
/// N5 M2 owner auth: password login + 30-day session tokens.
/// Contract (Worker M2.1 builder implements these endpoints):
///   GET  /api/auth/status   (public) -> { setup_required }
///   POST /api/auth/setup    (X-Vesper-Key) { password, device_label? } -> { token, expires_at }
///   POST /api/auth/login    { password, device_label? } -> { token, expires_at }
///   POST /api/auth/logout, POST /api/auth/logout-all,
///   GET  /api/auth/sessions (Bearer token)
///   POST /api/auth/exchange (X-Vesper-Key) -> { token, expires_at }
/// The password is sent once over HTTPS and never stored by the
/// client. The token is the only thing stored (DPAPI in the app).
/// </summary>
public sealed class AuthClient : IDisposable
{
    private readonly HttpClient _http;

    public AuthClient(HttpClient? http = null)
    {
        _http = http ?? new HttpClient
        {
            BaseAddress = new Uri(AthenaApi.WorkerBase),
            Timeout = TimeSpan.FromSeconds(30),
        };
    }

    public void Dispose() => _http.Dispose();

    /// <summary>
    /// GET /api/auth/status. Returns null when the endpoint is not
    /// deployed yet (HTTP 404) so callers can fall back to the site
    /// key instead of inventing an auth state.
    /// </summary>
    public async Task<AuthStatus?> GetStatusAsync(CancellationToken ct = default)
    {
        using var res = await _http.GetAsync(AthenaApi.AuthStatusPath, ct);
        if ((int)res.StatusCode == 404) return null;
        if (!res.IsSuccessStatusCode)
            throw new AthenaApiException((int)res.StatusCode,
                $"Could not read auth status (HTTP {(int)res.StatusCode}).");
        return await res.Content.ReadFromJsonAsync<AuthStatus>(ct);
    }

    /// <summary>
    /// POST /api/auth/setup (site key). Only when no owner exists
    /// yet — the Worker answers 409 once a password is set.
    /// </summary>
    public async Task<AuthTokenResponse> SetupAsync(
        string password, string apiKey, string? deviceLabel = null,
        CancellationToken ct = default)
    {
        if (!AuthValidation.IsValidPassword(password))
            throw new ArgumentException(
                "Password must be at least 8 characters.", nameof(password));
        using var req = new HttpRequestMessage(HttpMethod.Post, AthenaApi.AuthSetupPath);
        req.Headers.Add("X-Vesper-Key", apiKey);
        req.Content = JsonContent.Create(new
        {
            password,
            device_label = deviceLabel ?? "ATHENA app — laptop",
        });
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return (await res.Content.ReadFromJsonAsync<AuthTokenResponse>(ct))
            ?? throw new AthenaApiException(0, "Setup came back empty.");
    }

    /// <summary>POST /api/auth/login. 401 on a wrong password.</summary>
    public async Task<AuthTokenResponse> LoginAsync(
        string password, string? deviceLabel = null,
        CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, AthenaApi.AuthLoginPath);
        req.Content = JsonContent.Create(new
        {
            password,
            device_label = deviceLabel ?? "ATHENA app — laptop",
        });
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return (await res.Content.ReadFromJsonAsync<AuthTokenResponse>(ct))
            ?? throw new AthenaApiException(0, "Login came back empty.");
    }

    /// <summary>
    /// POST /api/auth/exchange (site key). Existing installs swap a
    /// stored site key for a token once, then delete the key.
    /// </summary>
    public async Task<AuthTokenResponse> ExchangeAsync(
        string apiKey, string? deviceLabel = null,
        CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, AthenaApi.AuthExchangePath);
        req.Headers.Add("X-Vesper-Key", apiKey);
        req.Content = JsonContent.Create(new
        {
            device_label = deviceLabel ?? "ATHENA app — laptop",
        });
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return (await res.Content.ReadFromJsonAsync<AuthTokenResponse>(ct))
            ?? throw new AthenaApiException(0, "Exchange came back empty.");
    }

    /// <summary>POST /api/auth/logout (Bearer token). Best effort — a failure here never blocks signing out locally.</summary>
    public async Task LogoutAsync(string token, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, AthenaApi.AuthLogoutPath);
        req.Headers.Add("Authorization", "Bearer " + token);
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
    }

    /// <summary>POST /api/auth/logout-all (Bearer token): revoke every session.</summary>
    public async Task LogoutAllAsync(string token, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, AthenaApi.AuthLogoutAllPath);
        req.Headers.Add("Authorization", "Bearer " + token);
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
    }

    /// <summary>GET /api/auth/sessions (Bearer token).</summary>
    public async Task<AuthSession[]> GetSessionsAsync(
        string token, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, AthenaApi.AuthSessionsPath);
        req.Headers.Add("Authorization", "Bearer " + token);
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return (await res.Content.ReadFromJsonAsync<AuthSession[]>(ct))
            ?? Array.Empty<AuthSession>();
    }

    /// <summary>
    /// Validates the current auth WITHOUT a model call and WITHOUT
    /// creating any rows: the server authenticates first, then
    /// rejects the empty request with HTTP 400 (P2-M1 rev7). 400
    /// therefore means the token/key is good; 401 means it is wrong.
    /// A token is tried first; the site key is the grace-period
    /// fallback. At least one must be present.
    /// </summary>
    public async Task<KeyValidationResult> ValidateAuthAsync(
        string? token, string? apiKey, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, AthenaApi.ChatPath);
        AuthHeaders.Apply(req, token, apiKey);
        req.Content = JsonContent.Create(new
        {
            messages = Array.Empty<object>(),
            conversation_id = "key-check",
        });

        using var res = await _http.SendAsync(req, ct);
        return (int)res.StatusCode switch
        {
            400 => KeyValidationResult.Valid,
            401 => KeyValidationResult.Invalid,
            429 => KeyValidationResult.RateLimited,
            _ => KeyValidationResult.Error,
        };
    }

    private static AthenaApiException MapError(int status) => new(status,
        status switch
        {
            401 => "Not authorised — the password was wrong, or the session has expired. Sign in again.",
            409 => "A password is already set — sign in instead of setting up again.",
            429 => "Too many attempts — wait a minute and try again.",
            500 => ErrorMessages.ServiceProblemRequest,
            _ => $"The service returned HTTP {status}.",
        });
}

/// <summary>
/// Which credential a request carries. During the M2 grace period
/// the Worker accepts EITHER a Bearer token OR the site key — a
/// token is always preferred when one is stored, and the key is
/// never sent alongside a token.
/// </summary>
public static class AuthHeaders
{
    /// <summary>True when a token is present (so the key is not needed).</summary>
    public static bool PrefersToken(string? token) =>
        !string.IsNullOrWhiteSpace(token);

    /// <summary>
    /// The single auth header for a request: Bearer token when one
    /// is stored, otherwise the site key. Throws when neither is
    /// present — callers must check HasAuth first.
    /// </summary>
    public static (string Name, string Value) Select(string? token, string? apiKey)
    {
        if (!string.IsNullOrWhiteSpace(token))
            return ("Authorization", "Bearer " + token.Trim());
        if (!string.IsNullOrWhiteSpace(apiKey))
            return ("X-Vesper-Key", apiKey.Trim());
        throw new ArgumentException("No token and no site key to authenticate with.");
    }

    public static void Apply(HttpRequestMessage req, string? token, string? apiKey)
    {
        var (name, value) = Select(token, apiKey);
        req.Headers.Add(name, value);
    }
}

/// <summary>Pure password rules for the M2 login/setup screens (no I/O, so fully testable).</summary>
public static class AuthValidation
{
    /// <summary>Contract minimum: 8 characters.</summary>
    public const int MinPasswordLength = 8;

    public static bool IsValidPassword(string? password) =>
        !string.IsNullOrEmpty(password) && password.Length >= MinPasswordLength;

    public static bool PasswordsMatch(string? a, string? b) =>
        string.Equals(a, b, StringComparison.Ordinal);

    /// <summary>
    /// Migration for existing installs: exchange the stored site key
    /// for a token exactly once — only when there is no token yet
    /// and a key is stored. Never when a token already exists.
    /// </summary>
    public static bool ShouldMigrate(bool hasToken, bool hasKey) =>
        !hasToken && hasKey;
}

/// <summary>
/// Builds the user message object for /api/chat. The via_voice tag
/// is client-asserted: it must be set ONLY for messages whose text
/// came from the microphone — never for typed text, never for
/// device-command prompts. The Worker enforces the voice-only
/// revoke guard on it, so a wrong tag is a security hole, not a
/// cosmetic bug. The tag is omitted entirely (not false) for typed
/// messages.
/// </summary>
public static class ChatMessageBuilder
{
    public static Dictionary<string, object> UserMessage(string text, bool viaVoice)
    {
        var msg = new Dictionary<string, object>
        {
            ["role"] = "user",
            ["content"] = text.Trim(),
        };
        if (viaVoice)
            msg["via_voice"] = true;
        return msg;
    }
}
