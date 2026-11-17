using Athena.Core;

namespace Athena;

/// <summary>
/// Session state shared by the MainWindow shell and all views:
/// the backend clients, the site key, the current conversation,
/// and the last loaded model info / backup snapshot.
/// </summary>
public sealed class AppState
{
    public ChatClient Chat { get; } = new();
    public DataClient Data { get; } = new();
    public string? ApiKey { get; set; }
    public string ConversationId { get; set; } = "";
    public ModelInfo? ModelInfo { get; set; }
    public BackupExport? Backup { get; set; }

    public event Action? ConversationChanged;

    // ----- Dynamic Island (N3) -----
    // One state machine for the whole app: ChatView reports
    // Thinking/Idle/Error/Offline from real chat events,
    // MainWindow reports connection changes, and the Island
    // window renders whatever the machine holds. Nothing else
    // may set a state — the machine rejects producers it does
    // not recognise (Listening/Speaking need voice in N4,
    // Working/Approval need device activity in N5).
    private readonly IslandStateMachine _island = new();

    public IslandStatus IslandStatus => _island.Current;

    public event Action<IslandStatus>? IslandStateChanged;

    /// <summary>Ask the machine for a new Island state; returns false when the caller may not produce it.</summary>
    public bool SetIslandState(IslandState state, IslandTriggerSource source, string detail)
    {
        var before = _island.Current;
        if (!_island.TrySet(state, source, detail)) return false;
        if (_island.Current != before)
            IslandStateChanged?.Invoke(_island.Current);
        return true;
    }

    /// <summary>Latest assistant reply, one line, for the Island's expanded view. Set by ChatView on each reply.</summary>
    public string? LastReplySnippet { get; private set; }

    public void SetLastReply(string reply)
    {
        var oneLine = reply.Replace('\n', ' ').Replace('\r', ' ').Trim();
        LastReplySnippet = oneLine.Length > 160 ? oneLine[..157] + "…" : oneLine;
    }

    /// <summary>Raised when a view asks for the Island to be shown again (Settings → Show the floating Island).</summary>
    public event Action? ShowIslandRequested;

    public void RequestShowIsland() => ShowIslandRequested?.Invoke();

    // ----- Voice (N4) -----
    // The service is created by MainWindow (it needs the UI thread
    // for SAPI) and may be null when SAPI is unavailable — views
    // must check before offering voice controls.
    public VoiceService? Voice { get; set; }

    /// <summary>Voice preferences, loaded from voice.json at startup. Output OFF by default.</summary>
    public VoiceSettings VoiceSettings { get; set; } = VoiceSettings.Defaults;

    /// <summary>Directory holding voice.json (set by MainWindow; %APPDATA%\Athena).</summary>
    public string VoiceSettingsDir { get; set; } = "";

    public void SaveVoiceSettings()
    {
        if (!string.IsNullOrWhiteSpace(VoiceSettingsDir))
            VoiceSettingsStore.Save(VoiceSettingsDir, VoiceSettings);
    }

    public void OpenConversation(string id)
    {
        ConversationId = id;
        ConversationChanged?.Invoke();
    }

    public void NewConversation() => OpenConversation(Guid.NewGuid().ToString());
}
