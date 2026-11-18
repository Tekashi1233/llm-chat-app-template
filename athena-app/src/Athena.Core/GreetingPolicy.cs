namespace Athena.Core;

/// <summary>
/// Why the window is being shown — the greeting decision keys off
/// this, so a tray restore or a normal launch can never greet.
/// </summary>
public enum WindowShowReason
{
    /// <summary>Normal launch (including autostart at login).</summary>
    Launch,

    /// <summary>Restored from the tray menu or a tray double-click.</summary>
    TrayRestore,

    /// <summary>Popped up because the wake word "athena" was heard.</summary>
    WakeWord,
}

/// <summary>
/// N8 wake greeting policy. Pure logic — no I/O, no SAPI — so the
/// harness can test it on Linux. The greeting ("Welcome, boss.")
/// is spoken only when the window was popped up by the wake word
/// and the user left the greeting on; a tray restore or a normal
/// launch never greets, whatever the setting says. The greeting
/// is local-only UI/speech, never a chat message: it never reaches
/// the model, the conversation history, or the memories. This
/// policy owns only the trigger decision; MainWindow owns the
/// speak/show side, and the wake word's own 3-second cooldown
/// (WakeWordTrigger) already prevents greeting spam.
/// </summary>
public static class GreetingPolicy
{
    /// <summary>The exact greeting, spoken aloud and shown as text when speech is unavailable.</summary>
    public const string GreetingText = "Welcome, boss.";

    /// <summary>True only for a wake-word pop-up with the greeting switched on.</summary>
    public static bool ShouldGreet(WindowShowReason reason, bool greetingEnabled) =>
        greetingEnabled && reason == WindowShowReason.WakeWord;
}
