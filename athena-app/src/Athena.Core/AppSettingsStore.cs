using System.Text.Json;

namespace Athena.Core;

/// <summary>
/// Persists AppSettings (N8) as plain JSON in a `settings.json`
/// file inside a caller-supplied directory — the app passes
/// %APPDATA%\Athena, next to the DPAPI config and voice.json.
/// Same pattern as VoiceSettingsStore: Load never throws (a
/// missing or corrupt file returns null and the caller falls
/// back to AppSettings.Defaults). Pure file I/O, no Windows
/// dependency, so the harness can round-trip it in a temp
/// directory on Linux.
/// </summary>
public static class AppSettingsStore
{
    private static string StorePath(string dir) =>
        Path.Combine(dir, "settings.json");

    public static AppSettings? Load(string dir)
    {
        try
        {
            var path = StorePath(dir);
            if (!File.Exists(path)) return null;
            var json = File.ReadAllText(path);
            return JsonSerializer.Deserialize<AppSettings>(json);
        }
        catch (Exception)
        {
            return null;
        }
    }

    public static void Save(string dir, AppSettings settings)
    {
        try
        {
            Directory.CreateDirectory(dir);
            var json = JsonSerializer.Serialize(settings);
            File.WriteAllText(StorePath(dir), json);
        }
        catch (Exception)
        {
            // App preferences are a nicety; never break the app over them.
        }
    }
}
