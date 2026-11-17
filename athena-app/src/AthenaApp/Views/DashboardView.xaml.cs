using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// Dashboard: greeting, connection, model, device, recent
/// conversations, memory overview, tools and recent activity —
/// all from real backend data, never invented.
/// </summary>
public partial class DashboardView : UserControl, IRefreshable
{
    private readonly AppState _state;
    private readonly Action<string> _navigate;

    public DashboardView(AppState state, Action<string> navigate)
    {
        _state = state;
        _navigate = navigate;
        InitializeComponent();
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e) =>
        await RefreshAsync(_state);

    public async Task RefreshAsync(AppState state)
    {
        ContentPanel.Children.Clear();
        ContentPanel.Children.Add(ViewHelpers.BodyText("Loading…"));

        // Greeting + connection.
        var hour = DateTime.Now.Hour;
        var part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
        var greetCard = ViewHelpers.Card($"Good {part}, Abdullah", out var greetBody);
        greetBody.Children.Add(ViewHelpers.BodyText(
            string.IsNullOrWhiteSpace(state.ApiKey)
                ? "No site key set — use “Set key” above."
                : "Site key stored (encrypted). Backend: " + AthenaApi.WorkerBase));
        ContentPanel.Children.Clear();
        ContentPanel.Children.Add(greetCard);

        // Model card.
        var modelCard = ViewHelpers.Card("Model", out var modelBody);
        try
        {
            state.ModelInfo ??= await state.Data.GetModelInfoAsync();
            var m = state.ModelInfo;
            if (m is null)
            {
                modelBody.Children.Add(ViewHelpers.MutedText("couldn't read model info"));
            }
            else
            {
                modelBody.Children.Add(ViewHelpers.BodyText("Assistant: " + (m.Assistant ?? "—")));
                modelBody.Children.Add(ViewHelpers.BodyText("Model: " + (m.ConfiguredModel ?? "—")));
                modelBody.Children.Add(ViewHelpers.BodyText("Reasoning: " + (m.ReasoningEffort ?? "—")));
                modelBody.Children.Add(ViewHelpers.BodyText("Tools: " + m.Tools.Length + " registered"));
                modelBody.Children.Add(ViewHelpers.BodyText("Memory: " + (m.Memory ?? "—")));
            }
        }
        catch (Exception ex)
        {
            modelBody.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
        ContentPanel.Children.Add(modelCard);

        // Device card.
        var deviceCard = ViewHelpers.Card("Paired laptop", out var deviceBody);
        try
        {
            if (string.IsNullOrWhiteSpace(state.ApiKey))
            {
                deviceBody.Children.Add(ViewHelpers.MutedText("Set your site key first to see device status."));
            }
            else
            {
                var status = await state.Data.GetDeviceStatusAsync(state.ApiKey);
                if (status is null)
                {
                    deviceBody.Children.Add(ViewHelpers.MutedText(ViewHelpers.NotEnabledDevice));
                }
                else
                {
                    deviceBody.Children.Add(DeviceSummaryLine(status));
                    if (!status.Online && status.Reason is not null)
                        deviceBody.Children.Add(ViewHelpers.BodyText("Reason: " + status.Reason));
                }
            }
        }
        catch (Exception ex)
        {
            deviceBody.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
        ContentPanel.Children.Add(deviceCard);

        // Recent conversations.
        var convCard = ViewHelpers.Card("Recent conversations", out var convBody);
        try
        {
            if (string.IsNullOrWhiteSpace(state.ApiKey))
            {
                convBody.Children.Add(ViewHelpers.MutedText("Set your site key first to see conversations."));
            }
            else
            {
                state.Backup ??= await state.Data.GetBackupAsync(state.ApiKey);
                var recent = state.Backup is null
                    ? new List<ConversationSummary>()
                    : ConversationStore.GroupMessages(state.Backup.Messages).Take(5).ToList();
                if (recent.Count == 0)
                {
                    convBody.Children.Add(ViewHelpers.MutedText("No conversations yet. Say hello in Chat."));
                }
                else
                {
                    foreach (var c in recent)
                        convBody.Children.Add(ConversationRow(c));
                }
            }
        }
        catch (Exception ex)
        {
            convBody.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
        ContentPanel.Children.Add(convCard);

        // Memory overview.
        var memCard = ViewHelpers.Card("Memory", out var memBody);
        try
        {
            if (string.IsNullOrWhiteSpace(state.ApiKey))
            {
                memBody.Children.Add(ViewHelpers.MutedText("Set your site key first to see memory."));
            }
            else
            {
                var list = await state.Data.GetMemoriesAsync(state.ApiKey, state.ConversationId);
                if (list is null)
                {
                    memBody.Children.Add(ViewHelpers.MutedText(ViewHelpers.NotEnabledMemories));
                }
                else
                {
                    var active = list.Memories.Count(m => m.Status == "active");
                    var superseded = list.Memories.Count(m => m.Status == "superseded");
                    memBody.Children.Add(ViewHelpers.BodyText(
                        $"{active} active, {superseded} superseded in this conversation."));
                }
            }
        }
        catch (Exception ex)
        {
            memBody.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
        ContentPanel.Children.Add(memCard);

        // Tools.
        var toolsCard = ViewHelpers.Card("Tools", out var toolsBody);
        var registered = state.ModelInfo is null
            ? -1
            : ToolCatalog.All.Count(t => t.IsRegistered(state.ModelInfo));
        toolsBody.Children.Add(ViewHelpers.BodyText(
            registered < 0
                ? $"{ToolCatalog.All.Length} tools in the catalogue — couldn't read model info."
                : $"{ToolCatalog.All.Length} tools in the catalogue, {registered} registered by the server."));
        ContentPanel.Children.Add(toolsCard);

        // Recent activity.
        var actCard = ViewHelpers.Card("Recent activity", out var actBody);
        try
        {
            if (string.IsNullOrWhiteSpace(state.ApiKey))
            {
                actBody.Children.Add(ViewHelpers.MutedText("Set your site key first to see activity."));
            }
            else
            {
                var list = await state.Data.GetDeviceActivityAsync(state.ApiKey, limit: 5);
                if (list is null)
                {
                    actBody.Children.Add(ViewHelpers.MutedText(ViewHelpers.NotEnabledActivity));
                }
                else if (list.Actions.Length == 0)
                {
                    actBody.Children.Add(ViewHelpers.MutedText("No activity recorded yet."));
                }
                else
                {
                    foreach (var a in list.Actions)
                    {
                        actBody.Children.Add(ViewHelpers.BodyText(
                            $"{ConversationStore.RelativeTime(a.CreatedAt)} — {a.Kind}: {a.Detail}"));
                    }
                }
            }
        }
        catch (Exception ex)
        {
            actBody.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
        ContentPanel.Children.Add(actCard);
    }

    private static TextBlock DeviceSummaryLine(DeviceStatus s)
    {
        if (s.Device is null)
            return ViewHelpers.BodyText("No laptop paired");
        var line = s.Online
            ? $"Online — {s.Device}"
            : $"Offline — {s.Device}";
        if (s.CpuPercent is not null)
            line += $" · CPU {s.CpuPercent.Value:F0}%";
        if (s.RamUsedGb is not null && s.RamTotalGb is not null)
            line += $" · RAM {s.RamUsedGb.Value:F1} of {s.RamTotalGb.Value:F1} GB";
        return ViewHelpers.BodyText(line);
    }

    private Button ConversationRow(ConversationSummary c)
    {
        var stack = new StackPanel();
        stack.Children.Add(ViewHelpers.TitleText(c.Title));
        stack.Children.Add(ViewHelpers.MutedText(
            $"{ConversationStore.RelativeTime(c.LastMessageAt)} · {c.MessageCount} messages"));
        stack.Children.Add(ViewHelpers.MutedText(c.LastSnippet));
        var button = ViewHelpers.RowButton(stack);
        button.Click += (_, _) =>
        {
            _state.OpenConversation(c.Id);
            _navigate("Chat");
        };
        return button;
    }
}
