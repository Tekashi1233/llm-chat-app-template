namespace Athena.Core;

/// <summary>
/// The Dynamic Island's states (N3). The rule that makes them
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
public enum IslandState
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
public enum IslandTriggerSource
{
    Chat,
    Voice,
    Device,
    System,
}

public sealed record IslandStatus(IslandState State, string Detail, DateTimeOffset UpdatedAt);

/// <summary>
/// Pure state machine for the Island. No UI, no I/O — fully
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
public sealed class IslandStateMachine
{
    public IslandStatus Current { get; private set; } =
        new(IslandState.Idle, "Idle", DateTimeOffset.UtcNow);

    public bool TrySet(IslandState next, IslandTriggerSource source, string detail)
    {
        if (next == Current.State)
        {
            Current = Current with { Detail = detail, UpdatedAt = DateTimeOffset.UtcNow };
            return true;
        }

        var allowed = next switch
        {
            IslandState.Idle =>
                !(Current.State == IslandState.Thinking && source != IslandTriggerSource.Chat),
            IslandState.Thinking => source == IslandTriggerSource.Chat,
            IslandState.Listening => source == IslandTriggerSource.Voice,
            IslandState.Speaking => source == IslandTriggerSource.Voice,
            IslandState.Working => source == IslandTriggerSource.Device,
            IslandState.Approval => source == IslandTriggerSource.Device,
            IslandState.Offline => true,
            IslandState.Error =>
                source is IslandTriggerSource.Chat or IslandTriggerSource.System,
            _ => false,
        };

        if (!allowed) return false;
        Current = new IslandStatus(next, detail, DateTimeOffset.UtcNow);
        return true;
    }
}
