namespace Athena.Core;

/// <summary>
/// Client-side conversation logic over /api/backup rows. Pure
/// functions — no network, no UI — so they are unit-testable on
/// Linux. Titles come from the first user message; nothing is
/// invented: a conversation with no user message yet is titled
/// "Conversation".
/// </summary>
public static class ConversationStore
{
    /// <summary>
    /// Groups backup messages by conversation, newest first.
    /// Messages with a blank conversation id are skipped.
    /// </summary>
    public static List<ConversationSummary> GroupMessages(
        IEnumerable<BackupMessage> messages)
    {
        var groups = messages
            .Where(m => !string.IsNullOrWhiteSpace(m.ConversationId))
            .GroupBy(m => m.ConversationId)
            .Select(g =>
            {
                var ordered = g.OrderBy(m => m.CreatedAtMs).ToArray();
                var firstUser = ordered.FirstOrDefault(
                    m => m.Role == "user" && !string.IsNullOrWhiteSpace(m.Content));
                var last = ordered[^1];
                return new ConversationSummary(
                    g.Key,
                    firstUser is null
                        ? "Conversation"
                        : TruncateTitle(firstUser.Content),
                    TruncateSnippet(last.Content),
                    last.CreatedAt,
                    ordered.Length);
            })
            .OrderByDescending(c => c.LastMessageAt)
            .ToList();
        return groups;
    }

    /// <summary>Case-insensitive title/snippet search over summaries.</summary>
    public static List<ConversationSummary> Search(
        IEnumerable<ConversationSummary> summaries, string query)
    {
        if (string.IsNullOrWhiteSpace(query))
            return summaries.ToList();
        var q = query.Trim();
        return summaries
            .Where(c =>
                c.Title.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                c.LastSnippet.Contains(q, StringComparison.OrdinalIgnoreCase))
            .ToList();
    }

    /// <summary>
    /// "just now", "5 minutes ago", "3 hours ago", "Yesterday",
    /// "4 days ago", otherwise "12 Nov 2026". Local time.
    /// </summary>
    public static string RelativeTime(DateTimeOffset when)
    {
        var age = DateTimeOffset.Now - when.ToLocalTime();
        if (age < TimeSpan.FromMinutes(1)) return "just now";
        if (age < TimeSpan.FromHours(1))
        {
            var m = (int)age.TotalMinutes;
            return m == 1 ? "1 minute ago" : $"{m} minutes ago";
        }
        if (age < TimeSpan.FromDays(1))
        {
            var h = (int)age.TotalHours;
            return h == 1 ? "1 hour ago" : $"{h} hours ago";
        }
        if (age < TimeSpan.FromDays(2)) return "Yesterday";
        if (age < TimeSpan.FromDays(7))
            return $"{(int)age.TotalDays} days ago";
        return when.ToLocalTime().ToString("d MMM yyyy");
    }

    public static string TruncateTitle(string? text)
    {
        var oneLine = (text ?? "").Trim().ReplaceLineEndings(" ");
        if (oneLine.Length <= 48) return oneLine;
        return oneLine[..47].TrimEnd() + "…";
    }

    public static string TruncateSnippet(string? text)
    {
        var oneLine = (text ?? "").Trim().ReplaceLineEndings(" ");
        if (oneLine.Length <= 80) return oneLine;
        return oneLine[..79].TrimEnd() + "…";
    }
}
