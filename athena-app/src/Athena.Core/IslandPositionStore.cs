using System.Text.Json;

namespace Athena.Core;

/// <summary>
/// Persists the Dynamic Island's position (N3) as plain JSON in
/// an `island.json` file inside a caller-supplied directory — the
/// app passes %APPDATA%\Athena, next to the DPAPI config. The
/// position is not a secret, so it is stored unencrypted. Load
/// never throws: a missing or corrupt file returns null and the
/// caller falls back to the default bottom-right spot. Pure file
/// I/O, no Windows dependency, so the harness can round-trip it
/// in a temp directory on Linux.
/// </summary>
public static class IslandPositionStore
{
    private static string StorePath(string dir) =>
        Path.Combine(dir, "island.json");

    public static IslandPosition? Load(string dir)
    {
        try
        {
            var path = StorePath(dir);
            if (!File.Exists(path)) return null;
            var json = File.ReadAllText(path);
            return JsonSerializer.Deserialize<IslandPosition>(json);
        }
        catch (Exception)
        {
            return null;
        }
    }

    public static void Save(string dir, IslandPosition position)
    {
        try
        {
            Directory.CreateDirectory(dir);
            var json = JsonSerializer.Serialize(position);
            File.WriteAllText(StorePath(dir), json);
        }
        catch (Exception)
        {
            // Position is a nicety; never break the app over it.
        }
    }
}
