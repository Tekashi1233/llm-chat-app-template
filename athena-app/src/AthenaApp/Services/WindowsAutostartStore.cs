using Athena.Core;
using Microsoft.Win32;

namespace Athena;

/// <summary>
/// N8: the real autostart store — the per-user Registry Run key
/// (HKCU\Software\Microsoft\Windows\CurrentVersion\Run, value
/// name "ATHENA"). Windows-only: Registry.CurrentUser throws
/// PlatformNotSupportedException anywhere else, so every call
/// site treats it as best-effort (try/catch, never fatal). On
/// this Linux VM the class compiles (net8.0-windows +
/// EnableWindowsTargeting) but can never run — the registry
/// behaviour is verified only by the in-memory fake in the test
/// harness, plus a real-laptop check.
/// </summary>
public sealed class WindowsAutostartStore : IAutostartStore
{
    public string? GetEntry()
    {
        using var key = Registry.CurrentUser.OpenSubKey(AutostartPolicy.RunKeyPath);
        return key?.GetValue(AutostartPolicy.ValueName) as string;
    }

    public void SetEntry(string value)
    {
        using var key = Registry.CurrentUser.CreateSubKey(AutostartPolicy.RunKeyPath);
        key.SetValue(AutostartPolicy.ValueName, value, RegistryValueKind.String);
    }

    public void RemoveEntry()
    {
        using var key = Registry.CurrentUser.OpenSubKey(AutostartPolicy.RunKeyPath, writable: true);
        key?.DeleteValue(AutostartPolicy.ValueName, throwOnMissingValue: false);
    }
}
