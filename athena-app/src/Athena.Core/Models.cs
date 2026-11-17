using System.Text.Json.Serialization;

namespace Athena.Core;

// ---- Shapes returned by the existing Worker API. Field names are
// snake_case on the wire, so every property carries its JSON name.
// All timestamps are Unix milliseconds (the Worker stores
// Date.now()). ----

/// <summary>GET /api/model-info (public, no key needed).</summary>
public sealed record ModelInfo(
    [property: JsonPropertyName("assistant")] string Assistant,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("configured_model")] string ConfiguredModel,
    [property: JsonPropertyName("reasoning_effort")] string ReasoningEffort,
    [property: JsonPropertyName("streaming")] bool Streaming,
    [property: JsonPropertyName("tools")] string[] Tools,
    [property: JsonPropertyName("auth")] string Auth,
    [property: JsonPropertyName("memory")] string Memory,
    [property: JsonPropertyName("status")] string Status);

/// <summary>One row of GET /api/backup.</summary>
public sealed record BackupMessage(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("conversation_id")] string ConversationId,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("content")] string Content,
    [property: JsonPropertyName("created_at")] long CreatedAtMs,
    [property: JsonPropertyName("origin")] string? Origin)
{
    public DateTimeOffset CreatedAt =>
        DateTimeOffset.FromUnixTimeMilliseconds(CreatedAtMs);
}

/// <summary>GET /api/backup (site key). Up to 5000 messages.</summary>
public sealed record BackupExport(
    [property: JsonPropertyName("exported_at")] string ExportedAt,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("messages")] BackupMessage[] Messages);

/// <summary>
/// One row of GET /api/memories (new N2 read endpoint, on the
/// n2-read-endpoints branch until approved). Same shape as the
/// Worker-side Memory type: kind fact|preference|project|plan,
/// source user|inferred, status active|superseded.
/// </summary>
public sealed record MemoryRecord(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("topic")] string Topic,
    [property: JsonPropertyName("fact")] string Fact,
    [property: JsonPropertyName("project")] string? Project,
    [property: JsonPropertyName("source")] string Source,
    [property: JsonPropertyName("created_at")] long CreatedAtMs,
    [property: JsonPropertyName("updated_at")] long UpdatedAtMs,
    [property: JsonPropertyName("superseded_by")] long? SupersededBy,
    [property: JsonPropertyName("status")] string Status)
{
    public DateTimeOffset CreatedAt =>
        DateTimeOffset.FromUnixTimeMilliseconds(CreatedAtMs);
    public DateTimeOffset UpdatedAt =>
        DateTimeOffset.FromUnixTimeMilliseconds(UpdatedAtMs);
    public string RecordedDate => CreatedAt.ToString("d MMM yyyy");
    public string SourceLabel => Source == "inferred"
        ? "ATHENA inferred it"
        : "You told ATHENA directly";
}

/// <summary>GET /api/memories response envelope.</summary>
public sealed record MemoryList(
    [property: JsonPropertyName("conversation_id")] string ConversationId,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("memories")] MemoryRecord[] Memories);

/// <summary>One disk from the Companion heartbeat.</summary>
public sealed record DeviceDisk(
    [property: JsonPropertyName("drive")] string Drive,
    [property: JsonPropertyName("free_gb")] double FreeGb,
    [property: JsonPropertyName("total_gb")] double TotalGb);

/// <summary>Network summary from the Companion heartbeat.</summary>
public sealed record DeviceNetwork(
    [property: JsonPropertyName("interface")] string? Interface,
    [property: JsonPropertyName("down_mbps")] double? DownMbps,
    [property: JsonPropertyName("up_mbps")] double? UpMbps);

/// <summary>
/// GET /api/devices/status (new N2 read endpoint, on the
/// n2-read-endpoints branch until approved). This is the JSON the
/// Worker's getDeviceStatus() already builds for the model — the
/// endpoint just returns it directly to the app.
/// </summary>
public sealed record DeviceStatus(
    [property: JsonPropertyName("online")] bool Online,
    [property: JsonPropertyName("device")] string? Device,
    [property: JsonPropertyName("reason")] string? Reason,
    [property: JsonPropertyName("last_seen_seconds_ago")] long? LastSeenSecondsAgo,
    [property: JsonPropertyName("cpu_percent")] double? CpuPercent,
    [property: JsonPropertyName("ram_used_gb")] double? RamUsedGb,
    [property: JsonPropertyName("ram_total_gb")] double? RamTotalGb,
    [property: JsonPropertyName("disks")] DeviceDisk[]? Disks,
    [property: JsonPropertyName("network")] DeviceNetwork? Network,
    [property: JsonPropertyName("uptime_hours")] double? UptimeHours);

/// <summary>
/// One row of GET /api/devices/activity (new N2 read endpoint, on
/// the n2-read-endpoints branch until approved): a device_actions
/// audit row, newest first. Read-only — the app never writes here.
/// </summary>
public sealed record DeviceAction(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("detail")] string Detail,
    [property: JsonPropertyName("approved")] int Approved,
    [property: JsonPropertyName("created_at")] long CreatedAtMs)
{
    public DateTimeOffset CreatedAt =>
        DateTimeOffset.FromUnixTimeMilliseconds(CreatedAtMs);
    public bool WasApproved => Approved == 1;
}

/// <summary>GET /api/devices/activity response envelope.</summary>
public sealed record DeviceActivityList(
    [property: JsonPropertyName("device")] string? Device,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("actions")] DeviceAction[] Actions);

/// <summary>A conversation derived from /api/backup rows (client-side).</summary>
public sealed record ConversationSummary(
    string Id,
    string Title,
    string LastSnippet,
    DateTimeOffset LastMessageAt,
    int MessageCount);
