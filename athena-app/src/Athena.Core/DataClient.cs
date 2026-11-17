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
        return (await res.Content.ReadFromJsonAsync<ModelInfo>(ct))
            ?? throw new AthenaApiException(0, "Model info came back empty.");
    }

    /// <summary>GET /api/backup. Site key. Up to 5000 messages.</summary>
    public async Task<BackupExport> GetBackupAsync(
        string apiKey, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, AthenaApi.BackupPath);
        req.Headers.Add("X-Vesper-Key", apiKey);
        using var res = await _http.SendAsync(req, ct);
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return (await res.Content.ReadFromJsonAsync<BackupExport>(ct))
            ?? throw new AthenaApiException(0, "Backup came back empty.");
    }

    /// <summary>
    /// GET /api/memories?conversation_id=… (site key). Returns null
    /// when the endpoint is not deployed yet (HTTP 404).
    /// </summary>
    public async Task<MemoryList?> GetMemoriesAsync(
        string apiKey, string conversationId, CancellationToken ct = default)
    {
        var path = $"{AthenaApi.MemoriesPath}" +
            $"?conversation_id={Uri.EscapeDataString(conversationId)}";
        using var req = new HttpRequestMessage(HttpMethod.Get, path);
        req.Headers.Add("X-Vesper-Key", apiKey);
        using var res = await _http.SendAsync(req, ct);
        if ((int)res.StatusCode == 404) return null;
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return await res.Content.ReadFromJsonAsync<MemoryList>(ct);
    }

    /// <summary>
    /// GET /api/devices/status (site key). Returns null when the
    /// endpoint is not deployed yet (HTTP 404).
    /// </summary>
    public async Task<DeviceStatus?> GetDeviceStatusAsync(
        string apiKey, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, AthenaApi.DeviceStatusPath);
        req.Headers.Add("X-Vesper-Key", apiKey);
        using var res = await _http.SendAsync(req, ct);
        if ((int)res.StatusCode == 404) return null;
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return await res.Content.ReadFromJsonAsync<DeviceStatus>(ct);
    }

    /// <summary>
    /// GET /api/devices/activity?limit=… (site key). Returns null
    /// when the endpoint is not deployed yet (HTTP 404).
    /// </summary>
    public async Task<DeviceActivityList?> GetDeviceActivityAsync(
        string apiKey, int limit = 50, CancellationToken ct = default)
    {
        var path = $"{AthenaApi.DeviceActivityPath}?limit={limit}";
        using var req = new HttpRequestMessage(HttpMethod.Get, path);
        req.Headers.Add("X-Vesper-Key", apiKey);
        using var res = await _http.SendAsync(req, ct);
        if ((int)res.StatusCode == 404) return null;
        if (!res.IsSuccessStatusCode)
            throw MapError((int)res.StatusCode);
        return await res.Content.ReadFromJsonAsync<DeviceActivityList>(ct);
    }

    private static AthenaApiException MapError(int status) => new(status,
        status switch
        {
            401 => "Not authorised — the site key is missing or wrong.",
            429 => "Too many requests — wait a minute and try again.",
            _ => $"The service returned HTTP {status}.",
        });
}
