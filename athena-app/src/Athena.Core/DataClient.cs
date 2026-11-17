using System.Net.Http.Json;

namespace Athena.Core;

/// <summary>
/// Read-only data client for the ATHENA backend: model info,
/// conversation backup, memories, device status and device activity.
/// Chat itself stays in ChatClient. Reads use a short timeout
/// (30 s) — they are quick lookups, not model turns.
///
/// The three N2 endpoints (memories, device status, device
/// activity) live on the n2-read-endpoints branch until the owner
/// approves them. Until they are deployed the Worker answers 404,
/// and these methods return null so the UI can show an honest
/// "not enabled yet" state instead of inventing data.
/// </summary>
public sealed class DataClient : IDisposable
{
    private readonly HttpClient _http;

    public DataClient(HttpClient? http = null)
    {
        _http = http ?? new HttpClient
        {
            BaseAddress = new Uri(AthenaApi.WorkerBase),
            Timeout = TimeSpan.FromSeconds(30),
        };
    }

    public void Dispose() => _http.Dispose();

    /// <summary>GET /api/model-info. Public — no key needed.</summary>
    public async Task<ModelInfo> GetModelInfoAsync(
        CancellationToken ct = default)
    {
        using var res = await _http.GetAsync(AthenaApi.ModelInfoPath, ct);
        if (!res.IsSuccessStatusCode)
            throw new AthenaApiException((int)res.StatusCode,
                $"Could not read model info (HTTP {(int)res.StatusCode}).");
        var info = (await res.Content.ReadFromJsonAsync<ModelInfo>(ct))
            ?? throw new AthenaApiException(0, "Model info came back empty.");
        // N6: a missing "tools" array must not NRE the views that
        // read Tools.Length / IsRegistered — normalise to empty.
        return info.Tools is null ? info with { Tools = Array.Empty<string>() } : info;
    }

    /// <summary>GET /api/backup. Bearer token when stored, else site key (grace). Up to 5000 messages.</summary>
    public async Task<BackupExport> GetBackupAsync(
        string? apiKey, CancellationToken ct = default, string? authToken = null)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, AthenaApi.BackupPath);
        AuthHeaders.Apply(req, authToken, apiKey);
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        var export = (await res.Content.ReadFromJsonAsync<BackupExport>(ct))
            ?? throw new AthenaApiException(0, "Backup came back empty.");
        // N6: a missing "messages" array must not NRE the views.
        return export.Messages is null
            ? export with { Messages = Array.Empty<BackupMessage>() }
            : export;
    }

    /// <summary>
    /// GET /api/memories?conversation_id=… (token, else site key). Returns null
    /// when the endpoint is not deployed yet (HTTP 404).
    /// </summary>
    public async Task<MemoryList?> GetMemoriesAsync(
        string? apiKey, string conversationId, CancellationToken ct = default, string? authToken = null)
    {
        var path = $"{AthenaApi.MemoriesPath}" +
            $"?conversation_id={Uri.EscapeDataString(conversationId)}";
        using var req = new HttpRequestMessage(HttpMethod.Get, path);
        AuthHeaders.Apply(req, authToken, apiKey);
        using var res = await _http.SendAsync(req, ct);
        if ((int)res.StatusCode == 404) return null;
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        var list = await res.Content.ReadFromJsonAsync<MemoryList>(ct);
        // N6: a missing "memories" array must not NRE the Vault.
        return list is null ? null
            : list.Memories is null
            ? list with { Memories = Array.Empty<MemoryRecord>() }
            : list;
    }

    /// <summary>
    /// GET /api/devices/status (token, else site key). Returns null when the
    /// endpoint is not deployed yet (HTTP 404).
    /// </summary>
    public async Task<DeviceStatus?> GetDeviceStatusAsync(
        string? apiKey, CancellationToken ct = default, string? authToken = null)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, AthenaApi.DeviceStatusPath);
        AuthHeaders.Apply(req, authToken, apiKey);
        using var res = await _http.SendAsync(req, ct);
        if ((int)res.StatusCode == 404) return null;
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return await res.Content.ReadFromJsonAsync<DeviceStatus>(ct);
    }

    /// <summary>
    /// GET /api/devices/activity?limit=… (token, else site key). Returns null
    /// when the endpoint is not deployed yet (HTTP 404).
    /// </summary>
    public async Task<DeviceActivityList?> GetDeviceActivityAsync(
        string? apiKey, int limit = 50, CancellationToken ct = default, string? authToken = null)
    {
        var path = $"{AthenaApi.DeviceActivityPath}?limit={limit}";
        using var req = new HttpRequestMessage(HttpMethod.Get, path);
        AuthHeaders.Apply(req, authToken, apiKey);
        using var res = await _http.SendAsync(req, ct);
        if ((int)res.StatusCode == 404) return null;
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        var list = await res.Content.ReadFromJsonAsync<DeviceActivityList>(ct);
        // N6: a missing "actions" array must not NRE the view.
        return list is null ? null
            : list.Actions is null
            ? list with { Actions = Array.Empty<DeviceAction>() }
            : list;
    }

    private static AthenaApiException MapError(int status) => new(status,
        status switch
        {
            401 => "Not authorised — sign in again, or the site key is missing or wrong.",
            429 => "Too many requests — wait a minute and try again.",
            500 => ErrorMessages.ServiceProblemRequest,
            _ => $"The service returned HTTP {status}.",
        });
}
