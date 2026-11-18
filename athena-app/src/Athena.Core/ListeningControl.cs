namespace Athena.Core;

/// <summary>
/// N8 tray listening controls: the persisted on/off switch and the
/// temporary pause, in one place so the tray menu and the Settings
/// "Wake up with 'athena'" toggle can never disagree. Pure logic —
/// no I/O, no SAPI — so the harness can test it on Linux.
///
/// Two different kinds of "off":
///   - Disabled (Enabled == false) is the persisted setting
///     (VoiceSettings.WakeWordEnabled, voice.json). It survives
///     restarts and is the same value the Settings toggle writes —
///     tray and Settings are just two handles on the one setting.
///   - Paused is temporary and in-memory only. It never touches
///     the persisted setting, a fresh launch starts unpaused, and
///     switching listening back on clears it ("listen now").
/// The listener should run exactly when IsListening is true.
/// </summary>
public sealed class ListeningControl
{
    /// <summary>The persisted on/off switch. True by default, matching VoiceSettings.WakeWordEnabled.</summary>
    public bool Enabled { get; private set; } = true;

    /// <summary>Temporary pause: in-memory only, never persisted. A fresh instance starts unpaused.</summary>
    public bool Paused { get; private set; }

    /// <summary>True when the listener should actually be running: enabled and not paused.</summary>
    public bool IsListening => Enabled && !Paused;

    /// <summary>
    /// Set the persisted switch. Switching back on clears any
    /// manual pause — enabling listening means "listen now".
    /// Switching off leaves Paused untouched (it is moot while
    /// disabled, and re-enabling clears it anyway).
    /// </summary>
    public void SetEnabled(bool enabled)
    {
        Enabled = enabled;
        if (enabled)
            Paused = false;
    }

    /// <summary>Pause the listener temporarily. No-op when disabled — pausing a disabled listener is meaningless.</summary>
    public void Pause()
    {
        if (Enabled)
            Paused = true;
    }

    /// <summary>Resume after a temporary pause. Never touches the persisted setting.</summary>
    public void Resume() => Paused = false;

    /// <summary>Flip the temporary pause. No-op when disabled.</summary>
    public void TogglePause()
    {
        if (!Enabled) return;
        Paused = !Paused;
    }
}
