using System.Windows;
using System.Windows.Controls;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// Read-only Memory Vault over the P2-M1 system: what ATHENA
/// remembers in this conversation, with provenance. Memories are
/// never altered or deleted from this view.
/// </summary>
public partial class MemoryVaultView : UserControl, IRefreshable
{
    private readonly AppState _state;
    private List<MemoryRecord> _all = new();
    private bool _loaded;

    public MemoryVaultView(AppState state)
    {
        _state = state;
        InitializeComponent();
    }

    public async Task RefreshAsync(AppState state)
    {
        _loaded = false;
        ListPanel.Children.Clear();
        ListPanel.Children.Add(ViewHelpers.MutedText("Loading…"));
        try
        {
            if (string.IsNullOrWhiteSpace(state.ApiKey))
            {
                ListPanel.Children.Clear();
                ListPanel.Children.Add(ViewHelpers.MutedText(
                    "Set your site key first to see memory."));
                return;
            }

            var list = await state.Data.GetMemoriesAsync(state.ApiKey, state.ConversationId);
            ListPanel.Children.Clear();
            if (list is null)
            {
                ListPanel.Children.Add(ViewHelpers.MutedText(ViewHelpers.NotEnabledMemories));
                return;
            }

            _all = list.Memories.ToList();
            _loaded = true;
            if (_all.Count == 0)
            {
                ListPanel.Children.Add(ViewHelpers.MutedText(
                    "Nothing remembered in this conversation yet. Ask ATHENA to remember something in chat."));
                return;
            }
            RenderList();
        }
        catch (Exception ex)
        {
            ListPanel.Children.Clear();
            ListPanel.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
    }

    private void Filter_Changed(object sender, RoutedEventArgs e)
    {
        if (_loaded) RenderList();
    }

    private void RenderList()
    {
        ListPanel.Children.Clear();
        var query = (SearchBox?.Text ?? "").Trim();
        var kind = SelectedValue(KindBox);
        var status = SelectedValue(StatusBox);
        var shown = _all.Where(m =>
            (kind == "All" || m.Kind == kind) &&
            (status == "All" || m.Status == status) &&
            (query.Length == 0 ||
                m.Topic.Contains(query, StringComparison.OrdinalIgnoreCase) ||
                m.Fact.Contains(query, StringComparison.OrdinalIgnoreCase))).ToList();

        if (shown.Count == 0)
        {
            ListPanel.Children.Add(ViewHelpers.MutedText("Nothing matches those filters."));
            return;
        }

        foreach (var m in shown)
        {
            var stack = new StackPanel();
            stack.Children.Add(ViewHelpers.TitleText(m.Topic));
            stack.Children.Add(ViewHelpers.MutedText(
                $"{m.Kind} · {m.Project ?? "—"} · {m.Status} · {m.RecordedDate}"));
            stack.Children.Add(ViewHelpers.MutedText(Snippet(m.Fact)));
            var button = ViewHelpers.RowButton(stack);
            button.Click += (_, _) => ShowDetail(m);
            ListPanel.Children.Add(button);
        }
    }

    private void ShowDetail(MemoryRecord m)
    {
        DetailPanel.Children.Clear();
        DetailPanel.Children.Add(ViewHelpers.TitleText(m.Topic));
        DetailPanel.Children.Add(ViewHelpers.BodyText(m.Fact));
        DetailPanel.Children.Add(ViewHelpers.MutedText("Kind: " + m.Kind));
        DetailPanel.Children.Add(ViewHelpers.MutedText("Project: " + (m.Project ?? "—")));
        DetailPanel.Children.Add(ViewHelpers.MutedText("Source: " + m.SourceLabel));
        DetailPanel.Children.Add(ViewHelpers.MutedText("Recorded: " + m.RecordedDate));
        DetailPanel.Children.Add(ViewHelpers.MutedText(
            "Updated: " + m.UpdatedAt.ToString("d MMM yyyy")));
        DetailPanel.Children.Add(ViewHelpers.MutedText("Status: " + m.Status));
        DetailPanel.Children.Add(ViewHelpers.MutedText("Id: " + m.Id));
    }

    private static string SelectedValue(ComboBox? box)
    {
        if (box?.SelectedItem is ComboBoxItem item && item.Content is string s)
            return s;
        return "All";
    }

    private static string Snippet(string fact)
    {
        var oneLine = fact.Trim().ReplaceLineEndings(" ");
        if (oneLine.Length <= 90) return oneLine;
        return oneLine[..89].TrimEnd() + "…";
    }
}
