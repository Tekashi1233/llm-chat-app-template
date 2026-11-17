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
