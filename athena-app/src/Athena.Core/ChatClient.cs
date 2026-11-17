using System.Net.Http.Json;

namespace Athena.Core;

/// <summary>
/// Talks to the existing ATHENA backend (the Cloudflare Worker) over
/// HTTPS. The backend is unchanged by N1: same routes, same auth
/// header, same conversation model. The app supplies the site key at
/// runtime (first-run setup, DPAPI-protected storage) — the key is
/// never baked into this code or the executable.
/// </summary>
public sealed class ChatClient : IDisposable
{
    private readonly HttpClient _http;

    public ChatClient(HttpClient? http = null)
    {
        _http = http ?? new HttpClient
        {
            BaseAddress = new Uri(AthenaApi.WorkerBase),
            // Streaming a reply can take a while; 120 s covers a slow
            // model turn without hanging forever on a dead connection.
            Timeout = TimeSpan.FromSeconds(120),
        };
    }

    public void Dispose() => _http.Dispose();

    /// <summary>
    /// Sends one user message and returns the assistant's full reply.
    /// The server loads this conversation's history itself, so a
    /// follow-up question works the same as on the website.
    /// Auth is a Bearer token when one is stored, else the site key
    /// (grace period — the Worker accepts either). viaVoice must be
    /// true ONLY when the text came from the microphone; it is the
    /// tag the Worker enforces the voice-only revoke guard on.
    /// </summary>
    public Task<string> SendMessageAsync(
        string text,
        string? apiKey,
        string conversationId,
        CancellationToken ct = default) =>
        SendMessageAsync(text, apiKey, conversationId, null, viaVoice: false, ct);

    public async Task<string> SendMessageAsync(
        string text,
        string? apiKey,
        string conversationId,
        string? authToken,
        bool viaVoice,
        CancellationToken ct = default)
    {
        if (!MessageValidation.IsValidUserMessage(text))
            throw new ArgumentException("Message is empty.", nameof(text));
        if (string.IsNullOrWhiteSpace(authToken) && string.IsNullOrWhiteSpace(apiKey))
            throw new ArgumentException("No token and no site key to authenticate with.", nameof(apiKey));

        using var req = new HttpRequestMessage(HttpMethod.Post, AthenaApi.ChatPath);
        AuthHeaders.Apply(req, authToken, apiKey);
        req.Content = JsonContent.Create(new
        {
            messages = new[] { ChatMessageBuilder.UserMessage(text, viaVoice) },
            conversation_id = conversationId,
        });

        using var res = await _http.SendAsync(
            req, HttpCompletionOption.ResponseHeadersRead, ct);
        var body = await res.Content.ReadAsStringAsync(ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);

        var reply = SseParser.Parse(body);
        return string.IsNullOrWhiteSpace(reply)
            ? "Sorry, I didn't manage to answer that. Could you try again?"
            : reply;
    }

    /// <summary>
    /// Validates a site key WITHOUT a model call and WITHOUT creating
    /// any rows: the server authenticates first, then rejects the
    /// empty request with HTTP 400 (P2-M1 rev7). 400 therefore means
    /// the key is good; 401 means it is wrong. Kept for the legacy
    /// key-paste fallback; new code should use ValidateAuthAsync,
    /// which prefers a stored token.
    /// </summary>
    public Task<KeyValidationResult> ValidateKeyAsync(
        string apiKey,
        CancellationToken ct = default) =>
        ValidateAuthAsync(null, apiKey, ct);

    /// <summary>
    /// Validates the current auth (token first, site key fallback)
    /// without a model call. See AuthClient.ValidateAuthAsync.
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
            401 => "Not authorised — sign in again, or the site key is missing or wrong.",
            429 => "Too many requests — wait a minute and try again.",
            _ => $"The service returned HTTP {status}.",
        });
}
