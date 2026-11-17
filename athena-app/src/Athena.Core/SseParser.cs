using System.Text;
using System.Text.Json;

namespace Athena.Core;

/// <summary>
/// Parses the Worker's Server-Sent Events stream into plain reply text.
/// Pure function: no network, no state — unit-tested in Athena.Tests.
/// The Worker streams one JSON object per `data:` line, each carrying
/// `choices[0].delta.content` (or `choices[0].message.content`, or a
/// top-level `response`); the stream ends with `data: [DONE]`.
/// </summary>
public static class SseParser
{
    public static string Parse(string sseBody)
    {
        var sb = new StringBuilder();
        foreach (var rawLine in sseBody.Split('\n'))
        {
            var line = rawLine.Trim();
            if (!line.StartsWith("data:", StringComparison.Ordinal))
                continue;
            var payload = line[5..].Trim();
            if (payload.Length == 0 || payload == "[DONE]")
                continue;
            try
            {
                using var doc = JsonDocument.Parse(payload);
                var root = doc.RootElement;
                string? text = null;
                if (root.TryGetProperty("choices", out var choices)
                    && choices.GetArrayLength() > 0)
                {
                    var first = choices[0];
                    if (first.TryGetProperty("delta", out var delta)
                        && delta.TryGetProperty("content", out var dc))
                        text = dc.GetString();
                    else if (first.TryGetProperty("message", out var msg)
                        && msg.TryGetProperty("content", out var mc))
                        text = mc.GetString();
                }
                text ??= root.TryGetProperty("response", out var resp)
                    ? resp.GetString()
                    : null;
                if (!string.IsNullOrEmpty(text))
                    sb.Append(text);
            }
            catch (JsonException)
            {
                // A malformed chunk must not lose the rest of the reply.
            }
        }
        return sb.ToString();
    }
}
