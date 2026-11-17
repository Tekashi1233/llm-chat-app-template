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

    // ----- Presence (was the N3 Dynamic Island) -----
    // One state machine for the whole app: ChatView reports
    // Thinking/Idle/Error/Offline from real chat events,
    // MainWindow reports connection changes, and the voice service
    // reports Listening/Speaking from real voice events. M1 drives
    // Working/Approval from real device activity: a device-command
    // turn (Devices quick action) shows Working, and a reply saying
    // a confirmation popup is waiting on the laptop shows Approval.
    // The MainWindow's reactor lighting follows whatever the
    // machine holds. Nothing else may set a state — the machine
    // rejects producers it does not recognise.
    private readonly PresenceStateMachine _presence = new();

    public PresenceStatus PresenceStatus => _presence.Current;

    public event Action<PresenceStatus>? PresenceChanged;

    /// <summary>Ask the machine for a new presence state; returns false when the caller may not produce it.</summary>
    public bool SetPresenceState(PresenceState state, PresenceSource source, string detail)
    {
        var before = _presence.Current;
        if (!_presence.TrySet(state, source, detail)) return false;
        if (_presence.Current != before)
            PresenceChanged?.Invoke(_presence.Current);
        return true;
    }

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
