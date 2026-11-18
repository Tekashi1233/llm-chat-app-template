namespace Athena.Core;

/// <summary>
/// Autostart (N8): ATHENA starts with Windows, hidden in the tray,
/// listening for "athena" when the wake-word setting is on. Per-user
/// Registry Run key only — HKCU\Software\Microsoft\Windows\CurrentVersion\Run,
/// value name "ATHENA", value = quoted exe path + " --tray". No
/// Startup-folder shortcut, no admin rights, no all-users install.
/// All logic here is pure (no registry access) and testable on Linux
/// behind the IAutostartStore interface; the real store lives in the
/// app (Services/WindowsAutostartStore.cs) and is Windows-only.
/// Privacy: nothing here records, stores or transmits audio — the
/// --tray launch only starts the existing local SAPI listener, and
/// only when VoiceSettings.WakeWordEnabled is on (see
/// MainWindow.CreateWakeWordService, which gates the start).
/// </summary>
public interface IAutostartStore
{
    /// <summary>Raw Run-key value for "ATHENA", or null when the entry does not exist.</summary>
    string? GetEntry();
    void SetEntry(string value);
    void RemoveEntry();
}

public static class AutostartPolicy
{
    public const string ValueName = "ATHENA";
    public const string Argument = "--tray";
    public const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";

    /// <summary>Build the Run-key value: quoted exe path + " --tray". Paths with spaces need the quotes.</summary>
    public static string BuildEntryValue(string exePath) =>
        "\"" + exePath + "\" " + Argument;

    /// <summary>
    /// Extract the exe path from a Run-key value. Handles a quoted
    /// path ("C:\Program Files\Athena\Athena.exe" --tray) and an
    /// unquoted one (C:\Athena\Athena.exe --tray). Returns null when
    /// the value is blank or unparseable — the caller must leave
    /// such an entry alone rather than delete something it cannot
    /// understand.
    /// </summary>
    public static string? ParseExePath(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return null;
        var v = value.Trim();
        if (v[0] == '"')
        {
            var close = v.IndexOf('"', 1);
            if (close <= 1) return null;
            var path = v[1..close];
            return string.IsNullOrWhiteSpace(path) ? null : path;
        }
        // Unquoted: the exe path runs to the first whitespace (an
        // argument, if any, follows).
        var end = v.IndexOfAny(new[] { ' ', '\t' });
        return end < 0 ? v : v[..end];
    }

    /// <summary>True when the value carries --tray as its own argument (case-insensitive).</summary>
    public static bool HasTrayArgument(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return false;
        return value.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries)
            .Any(part => string.Equals(part, Argument, StringComparison.OrdinalIgnoreCase));
    }

    /// <summary>True when the entry exists, points at this exe, and carries --tray.</summary>
    public static bool IsEnabled(IAutostartStore store, string exePath)
    {
        var entry = store.GetEntry();
        if (entry is null) return false;
        return string.Equals(ParseExePath(entry), exePath, StringComparison.OrdinalIgnoreCase)
            && HasTrayArgument(entry);
    }

    /// <summary>
    /// Register autostart for this exe. Writes only when the entry
    /// is missing or wrong — a launch must not re-write the key
    /// blindly every time. Returns true when the entry was written.
    /// </summary>
    public static bool EnsureEnabled(IAutostartStore store, string exePath)
    {
        if (IsEnabled(store, exePath)) return false;
        store.SetEntry(BuildEntryValue(exePath));
        return true;
    }

    /// <summary>Remove the entry when present. Returns true when something was removed.</summary>
    public static bool EnsureDisabled(IAutostartStore store)
    {
        if (store.GetEntry() is null) return false;
        store.RemoveEntry();
        return true;
    }

    /// <summary>
    /// Remove a stale entry: one whose exe path parses cleanly but
    /// the file no longer exists (moved/deleted install). An
    /// unparseable entry is left alone — it may belong to something
    /// else. Returns true when an entry was removed.
    /// </summary>
    public static bool CleanStaleEntry(IAutostartStore store, Func<string, bool> fileExists)
    {
        var entry = store.GetEntry();
        if (entry is null) return false;
        var path = ParseExePath(entry);
        if (path is null) return false;
        if (fileExists(path)) return false;
        store.RemoveEntry();
        return true;
    }
}

/// <summary>
/// Startup options (N8), parsed from the command line. --tray
/// (case-insensitive, anywhere in the args) means: start hidden
/// with the tray icon live — the window stays hidden until
/// "athena" or the tray menu shows it. Whether the wake-word
/// listener actually starts is NOT decided here: it follows the
/// persisted VoiceSettings.WakeWordEnabled, exactly as a normal
/// launch does (MainWindow.CreateWakeWordService gates on it), so
/// a --tray launch with wake-word off sits quietly in the tray
/// and does not listen. A normal launch (double-click, no arg)
/// shows the window.
/// </summary>
public sealed class StartupOptions
{
    public bool StartHidden { get; init; }

    public static StartupOptions Default => new() { StartHidden = false };

    public static StartupOptions Parse(IEnumerable<string> args) =>
        new()
        {
            StartHidden = args.Any(a =>
                string.Equals(a, AutostartPolicy.Argument, StringComparison.OrdinalIgnoreCase)),
        };
}
