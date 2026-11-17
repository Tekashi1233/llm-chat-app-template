namespace Athena.Core;

/// <summary>
/// Client-side message rules. Mirrors the server rule (P2-M1 rev7):
/// only a non-empty user message is ever sent. Enforcing it here too
/// means an empty request is rejected before any network traffic.
/// </summary>
public static class MessageValidation
{
    public static bool IsValidUserMessage(string? text) =>
        !string.IsNullOrWhiteSpace(text);
}
