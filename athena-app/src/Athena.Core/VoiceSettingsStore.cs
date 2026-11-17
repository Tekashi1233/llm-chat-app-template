using System.Text.Json;

namespace Athena.Core;

/// <summary>
/// Persists VoiceSettings (N4) as plain JSON in a `voice.json`
/// file inside a caller-supplied directory — the app passes
/// %APPDATA%\Athena, next to the DPAPI config and island.json.
/// Same pattern as the other small JSON stores: Load never throws (a
/// missing or corrupt file returns null and the caller falls
/// back to VoiceSettings.Defaults). Pure file I/O, no Windows
/// dependency, so the harness can round-trip it in a temp
/// directory on Linux.
/// </summary>
public static class VoiceSettingsStore
{
    private static string StorePath(string dir) =>
        Path.Combine(dir, "voice.json");

    public static VoiceSettings? Load(string dir)
    {
        try
        {
            var path = StorePath(dir);
            if (!File.Exists(path)) return null;
            var json = File.ReadAllText(path);
            return JsonSerializer.Deserialize<VoiceSettings>(json)?.Clamped();
        }
        catch (Exception)
        {
            return null;
        }
    }

    public static void Save(string dir, VoiceSettings settings)
    {
        try
        {
            Directory.CreateDirectory(dir);
            var json = JsonSerializer.Serialize(settings.Clamped());
            File.WriteAllText(StorePath(dir), json);
        }
        catch (Exception)
        {
            // Voice preferences are a nicety; never break the app over them.
        }
    }
}
