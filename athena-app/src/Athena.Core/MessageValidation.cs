namespace Athena.Core;

/// <summary>
/// Client-side message rules. Mirrors the server rule (P2-M1 rev7):
/// only a non-empty user message is ever sent. Enforcing it here too
/// means an empty request is rejected before any network traffic.
/// N6: a length cap as well — an unbounded paste is rejected
/// client-side instead of being sent to the model as a huge payload.
/// The server remains authoritative; this is only the first check.
/// </summary>
public static class MessageValidation
{
    /// <summary>Longest user message the app will send (characters).</summary>
    public const int MaxMessageLength = 8000;

    public static bool IsValidUserMessage(string? text) =>
        !string.IsNullOrWhiteSpace(text);

    public static bool IsWithinLengthLimit(string? text) =>
        text is null || text.Length <= MaxMessageLength;
}
