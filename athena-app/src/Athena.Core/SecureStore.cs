using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Athena.Core;

/// <summary>
/// Local settings for the ATHENA app, kept in %APPDATA%\Athena.
/// The site key and the owner session token are stored encrypted
/// with Windows DPAPI (current-user scope) via CryptProtectData —
/// they are NEVER written in plain text, NEVER put in source code,
/// and NEVER embedded in the executable.
/// DPAPI is Windows-only: on any other OS these methods throw a clear
/// PlatformNotSupportedException instead of silently falling back to
/// an insecure store. The conversation id is not a secret and is
/// stored in plain text next to it.
///
/// N5 M2: the token is the primary credential (password login).
/// The site key is kept only for the grace period / migration:
/// on startup a stored key is exchanged once for a token and then
/// deleted from the config (see AuthValidation.ShouldMigrate).
/// </summary>
public static class SecureStore
{
    private static string ConfigDir =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "Athena");

    private static string ConfigPath => Path.Combine(ConfigDir, "config.json");

    private sealed record AppConfig(
        string? ProtectedKey, string ConversationId, string? ProtectedToken = null);

    public static string GetOrCreateConversationId()
    {
        var cfg = ReadConfig();
        if (cfg is not null && Guid.TryParse(cfg.ConversationId, out _))
            return cfg.ConversationId;

        var id = Guid.NewGuid().ToString();
        WriteConfig(new AppConfig(cfg?.ProtectedKey, id, cfg?.ProtectedToken));
        return id;
    }

    /// <summary>Returns the stored key, or null when none is stored yet.</summary>
    public static string? TryLoadKey()
    {
        try
        {
            var cfg = ReadConfig();
            if (cfg?.ProtectedKey is null) return null;
            return Unprotect(Convert.FromBase64String(cfg.ProtectedKey));
        }
        catch (Exception ex) when (
            ex is FormatException or CryptographicException or DllNotFoundException
            or EntryPointNotFoundException or PlatformNotSupportedException)
        {
            // A corrupt or unreadable store behaves as "no key stored":
            // the user is asked to set the key again. Nothing is lost —
            // the backend still holds the conversation history.
            return null;
        }
    }

    public static void SaveKey(string key)
    {
        var cfg = ReadConfig();
        var id = cfg is not null && Guid.TryParse(cfg.ConversationId, out _)
            ? cfg.ConversationId
            : Guid.NewGuid().ToString();
        WriteConfig(new AppConfig(
            Convert.ToBase64String(Protect(key)),
            id, cfg?.ProtectedToken));
    }

    /// <summary>
    /// Deletes the stored site key (M2 migration: after a successful
    /// exchange the key must not linger in the config). The token
    /// and conversation id are kept.
    /// </summary>
    public static void DeleteKey()
    {
        var cfg = ReadConfig();
        if (cfg is null) return;
        var id = Guid.TryParse(cfg.ConversationId, out _)
            ? cfg.ConversationId
            : Guid.NewGuid().ToString();
        WriteConfig(new AppConfig(null, id, cfg.ProtectedToken));
    }

    /// <summary>Returns the stored owner session token, or null when not signed in.</summary>
    public static string? TryLoadToken()
    {
        try
        {
            var cfg = ReadConfig();
            if (cfg?.ProtectedToken is null) return null;
            return Unprotect(Convert.FromBase64String(cfg.ProtectedToken));
        }
        catch (Exception ex) when (
            ex is FormatException or CryptographicException or DllNotFoundException
            or EntryPointNotFoundException or PlatformNotSupportedException)
        {
            return null;
        }
    }

    public static void SaveToken(string token)
    {
        var cfg = ReadConfig();
        var id = cfg is not null && Guid.TryParse(cfg.ConversationId, out _)
            ? cfg.ConversationId
            : Guid.NewGuid().ToString();
        WriteConfig(new AppConfig(
            cfg?.ProtectedKey,
            id,
            Convert.ToBase64String(Protect(token))));
    }

    /// <summary>Deletes the stored token (sign out). The conversation id is kept.</summary>
    public static void DeleteToken()
    {
        var cfg = ReadConfig();
        if (cfg is null) return;
        var id = Guid.TryParse(cfg.ConversationId, out _)
            ? cfg.ConversationId
            : Guid.NewGuid().ToString();
        WriteConfig(new AppConfig(cfg.ProtectedKey, id, null));
    }

    private static AppConfig? ReadConfig()
    {
        if (!File.Exists(ConfigPath)) return null;
        var json = File.ReadAllText(ConfigPath);
        return JsonSerializer.Deserialize<AppConfig>(json);
    }

    private static void WriteConfig(AppConfig cfg)
    {
        Directory.CreateDirectory(ConfigDir);
        File.WriteAllText(ConfigPath,
            JsonSerializer.Serialize(cfg, new JsonSerializerOptions
            {
                WriteIndented = true,
            }));
    }

    // ---- DPAPI (crypt32.dll) via P/Invoke; no extra NuGet package ----

    private const uint CRYPTPROTECT_UI_FORBIDDEN = 0x1;

    [StructLayout(LayoutKind.Sequential)]
    private struct DataBlob
    {
        public int cbData;
        public IntPtr pbData;
    }

    [DllImport("crypt32.dll", SetLastError = true)]
    private static extern bool CryptProtectData(
        ref DataBlob pDataIn,
        string? szDataDescr,
        IntPtr pOptionalEntropy,
        IntPtr pvReserved,
        IntPtr pPromptStruct,
        uint dwFlags,
        out DataBlob pDataOut);

    [DllImport("crypt32.dll", SetLastError = true)]
    private static extern bool CryptUnprotectData(
        ref DataBlob pDataIn,
        out string? pszDataDescr,
        IntPtr pOptionalEntropy,
        IntPtr pvReserved,
        IntPtr pPromptStruct,
        uint dwFlags,
        out DataBlob pDataOut);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr LocalFree(IntPtr hMem);

    private static byte[] Protect(string plainText)
    {
        var plain = Encoding.UTF8.GetBytes(plainText);
        var inBlob = ToBlob(plain);
        try
        {
            if (!CryptProtectData(
                    ref inBlob, null, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero,
                    CRYPTPROTECT_UI_FORBIDDEN, out var outBlob))
                throw new CryptographicException(
                    "DPAPI encryption failed (error " + Marshal.GetLastWin32Error() + ").");
            try
            {
                var cipher = new byte[outBlob.cbData];
                Marshal.Copy(outBlob.pbData, cipher, 0, cipher.Length);
                return cipher;
            }
            finally
            {
                LocalFree(outBlob.pbData);
            }
        }
        finally
        {
            Marshal.FreeHGlobal(inBlob.pbData);
        }
    }

    private static string Unprotect(byte[] cipher)
    {
        var inBlob = ToBlob(cipher);
        try
        {
            if (!CryptUnprotectData(
                    ref inBlob, out _, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero,
                    CRYPTPROTECT_UI_FORBIDDEN, out var outBlob))
                throw new CryptographicException(
                    "DPAPI decryption failed (error " + Marshal.GetLastWin32Error() + ").");
            try
            {
                var plain = new byte[outBlob.cbData];
                Marshal.Copy(outBlob.pbData, plain, 0, plain.Length);
                return Encoding.UTF8.GetString(plain);
            }
            finally
            {
                LocalFree(outBlob.pbData);
            }
        }
        finally
        {
            Marshal.FreeHGlobal(inBlob.pbData);
        }
    }

    private static DataBlob ToBlob(byte[] bytes)
    {
        var ptr = Marshal.AllocHGlobal(bytes.Length);
        Marshal.Copy(bytes, 0, ptr, bytes.Length);
        return new DataBlob { cbData = bytes.Length, pbData = ptr };
    }
}
