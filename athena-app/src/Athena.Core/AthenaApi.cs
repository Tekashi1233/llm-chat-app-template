using System.Text.Json.Serialization;

namespace Athena.Core;

/// <summary>
/// Contract for the ATHENA backend: the existing Cloudflare Worker.
/// Nothing here is new — the Worker, its routes, its model and its
/// database are unchanged by N1. This class only names them.
/// </summary>
public static class AthenaApi
{
    public const string WorkerBase =
        "https://llm-chat-app-template.thanosabdulla.workers.dev";
    public const string ChatPath = "/api/chat";
    public const string ModelInfoPath = "/api/model-info";
    public const string BackupPath = "/api/backup";
    // N2 read endpoints: on the n2-read-endpoints branch until the
    // owner approves them. DataClient returns null on HTTP 404.
    public const string MemoriesPath = "/api/memories";
    public const string DeviceStatusPath = "/api/devices/status";
    public const string DeviceActivityPath = "/api/devices/activity";
    // N5 M2 owner auth (password + session tokens). Implemented on
    // the Worker by the M2.1 builder; the app codes against this
    // contract exactly. Until the endpoints are deployed the Worker
    // answers 404 and the clients fall back to the site key.
    public const string AuthStatusPath = "/api/auth/status";
    public const string AuthSetupPath = "/api/auth/setup";
    public const string AuthLoginPath = "/api/auth/login";
    public const string AuthLogoutPath = "/api/auth/logout";
    public const string AuthLogoutAllPath = "/api/auth/logout-all";
    public const string AuthSessionsPath = "/api/auth/sessions";
    public const string AuthExchangePath = "/api/auth/exchange";
}

/// <summary>An error returned by the backend, with its HTTP status.</summary>
public sealed class AthenaApiException : Exception
{
    public int StatusCode { get; }

    public AthenaApiException(int statusCode, string message) : base(message) =>
        StatusCode = statusCode;
}

/// <summary>Result of checking a site key without spending a model call.</summary>
public enum KeyValidationResult
{
    Valid,
    Invalid,
    RateLimited,
    Error,
}

/// <summary>
/// N5 M2 owner auth.
/// GET /api/auth/status (public). setup_required is true until the
/// owner sets a password for the first time.
/// </summary>
public sealed record AuthStatus(
    [property: JsonPropertyName("setup_required")] bool SetupRequired);

/// <summary>
/// N5 M2: what login / setup / exchange return — a 30-day session
/// token, shown once. The client stores it (DPAPI in the app,
/// localStorage on the website) and sends it as
/// Authorization: Bearer &lt;token&gt;.
/// </summary>
public sealed record AuthTokenResponse(
    [property: JsonPropertyName("token")] string Token,
    [property: JsonPropertyName("expires_at")] string ExpiresAt);

/// <summary>One row of GET /api/auth/sessions.</summary>
public sealed record AuthSession(
    [property: JsonPropertyName("device_label")] string? DeviceLabel,
    [property: JsonPropertyName("created_at")] string? CreatedAt,
    [property: JsonPropertyName("expires_at")] string? ExpiresAt,
    [property: JsonPropertyName("last_seen")] string? LastSeen);
