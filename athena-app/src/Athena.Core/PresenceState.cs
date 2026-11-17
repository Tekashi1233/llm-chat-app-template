namespace Athena.Core;

/// <summary>
/// The presence light's states (N3). The rule that makes them
/// honest: a state may only be entered by the event that actually
/// produces it. Listening/Speaking come only from voice input/
/// output (N4 — no producer exists yet, so they are unreachable
/// in N3); Working and Approval only from Companion device
/// activity (N5). Thinking comes only from a chat request in
/// flight; Offline only from a real connectivity failure; Error
/// only from a real failed request. The machine enforces this —
/// a caller asking for a state it cannot produce gets `false`
/// and the displayed state does not change.
/// </summary>
public enum PresenceState
{
    Idle,
    Listening,
    Thinking,
    Speaking,
    Working,
    Approval,
    Offline,
    Error,
}

/// <summary>Who is reporting the state change — decides what is allowed.</summary>
public enum PresenceSource
{
    Chat,
    Voice,
    Device,
    System,
}

public sealed record PresenceStatus(PresenceState State, string Detail, DateTimeOffset UpdatedAt);

/// <summary>
/// Pure state machine for the presence light. No UI, no I/O — fully
/// testable on Linux. Transitions:
/// - Idle: from anything except Thinking (only Chat completes a
///   Thinking turn; System must not clear it mid-request).
/// - Thinking: only from Chat.
/// - Listening / Speaking: only from Voice (N4).
/// - Working / Approval: only from Device (N5).
/// - Offline: from any source (connectivity loss is always real).
/// - Error: from Chat or System (a failed request).
/// - Same state: updates the detail text, returns true.
/// Anything else returns false and leaves the state untouched.
/// </summary>
public sealed class PresenceStateMachine
{
    public PresenceStatus Current { get; private set; } =
        new(PresenceState.Idle, "Idle", DateTimeOffset.UtcNow);

    public bool TrySet(PresenceState next, PresenceSource source, string detail)
    {
        if (next == Current.State)
        {
            Current = Current with { Detail = detail, UpdatedAt = DateTimeOffset.UtcNow };
            return true;
        }

        var allowed = next switch
        {
            PresenceState.Idle =>
                !(Current.State == PresenceState.Thinking && source != PresenceSource.Chat),
            PresenceState.Thinking => source == PresenceSource.Chat,
            PresenceState.Listening => source == PresenceSource.Voice,
            PresenceState.Speaking => source == PresenceSource.Voice,
            PresenceState.Working => source == PresenceSource.Device,
            PresenceState.Approval => source == PresenceSource.Device,
            PresenceState.Offline => true,
            PresenceState.Error =>
                source is PresenceSource.Chat or PresenceSource.System,
            _ => false,
        };

        if (!allowed) return false;
        Current = new PresenceStatus(next, detail, DateTimeOffset.UtcNow);
        return true;
    }
}
