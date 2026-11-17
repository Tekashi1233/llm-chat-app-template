namespace Athena.Core;

/// <summary>One tap-to-run device command: its label and the chat prompt it sends.</summary>
public sealed record DeviceQuickAction(string Label, string Prompt);

/// <summary>
/// M1: the device commands the app may send with one tap. These are
/// prompts, not raw commands: they go through chat, the model routes
/// them to the existing device tools, and the Companion on the laptop
/// enforces its own allowlist and shows its Allow popup for anything
/// that writes. The app never bypasses any of that — this list only
/// decides which shortcuts the Devices view offers.
/// </summary>
public static class DeviceQuickActions
{
    public static readonly DeviceQuickAction[] All =
    {
        new("Ping google.com", "Ping google.com"),
        new("Network info", "Show network info for the laptop"),
        new("List installed programs", "List the programs installed on the laptop"),
        new("Open Notepad", "Open Notepad on the laptop"),
    };

    /// <summary>Only a prompt from the list above may go down the device-command path.</summary>
    public static bool IsApprovedPrompt(string prompt) =>
        All.Any(a => string.Equals(a.Prompt, prompt, StringComparison.Ordinal));
}

/// <summary>
/// M1: signals in a chat reply that name real device activity. The
/// Worker answers a file-write/command turn that is still waiting
/// with a fixed sentence (see runDeviceCommand in src/devices.ts);
/// matching that exact sentence is the only signal the app has that
/// the laptop is holding a command for the owner's on-screen
/// approval. If the Worker ever rewords it, the match simply stops
/// firing — the presence stays on Working/Idle instead of Approval.
/// </summary>
public static class DeviceReplySignals
{
    public const string ApprovalWaitingPhrase = "confirmation popup is waiting";

    public static bool IsApprovalWaiting(string? reply) =>
        !string.IsNullOrEmpty(reply)
        && reply.Contains(ApprovalWaitingPhrase, StringComparison.Ordinal);
}
