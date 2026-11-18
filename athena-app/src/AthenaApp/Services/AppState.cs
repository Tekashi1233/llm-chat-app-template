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
    public AuthClient Auth { get; } = new();
    public string? ApiKey { get; set; }
    /// <summary>N5 M2 owner session token (DPAPI-encrypted in config.json). Preferred over ApiKey whenever present.</summary>
    public string? AuthToken { get; set; }
    /// <summary>True when the app can authenticate at all — token or (grace-period) site key.</summary>
    public bool HasAuth =>
        !string.IsNullOrWhiteSpace(AuthToken) || !string.IsNullOrWhiteSpace(ApiKey);
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

    /// <summary>N7 wake-word listener (its own SAPI engine), created by MainWindow; null when SAPI/mic unavailable.</summary>
    public WakeWordService? WakeWord { get; set; }

    /// <summary>N7: raised when the wake-word toggle in Settings changes, so MainWindow can start/stop the listener live.</summary>
    public event Action? WakeWordSettingChanged;

    public void NotifyWakeWordSettingChanged() => WakeWordSettingChanged?.Invoke();

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
