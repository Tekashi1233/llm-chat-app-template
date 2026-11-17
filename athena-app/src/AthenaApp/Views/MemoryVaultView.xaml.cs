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
        // N5 M3: the detail pane belongs to the previous load — a
        // new load starts with it cleared, so a stale memory is
        // never shown as current while the list reloads.
        ShowNoMemorySelected();
        try
        {
            if (!state.HasAuth)
            {
                ListPanel.Children.Clear();
                ListPanel.Children.Add(ViewHelpers.MutedText(
                    "Sign in first to see memory."));
                return;
            }

            var list = await state.Data.GetMemoriesAsync(state.ApiKey, state.ConversationId, authToken: state.AuthToken);
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
            // N5 M3: a failed refresh never leaves a blank panel
            // and never keeps stale data on screen — the list and
            // the cached rows are cleared, the detail pane goes
            // back to its placeholder, and the panel shows what
            // happened plus what to do next.
            _all = new();
            _loaded = false;
            ListPanel.Children.Clear();
            ListPanel.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
            ListPanel.Children.Add(ViewHelpers.MutedText(ViewHelpers.RetryHintReopen));
            ShowNoMemorySelected();
        }
    }

    private void ShowNoMemorySelected()
    {
        DetailPanel.Children.Clear();
        DetailPanel.Children.Add(ViewHelpers.MutedText(ViewHelpers.NoMemorySelected));
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
                // N6: a row with a missing topic/fact must not NRE
                // the filter — it simply does not match.
                (m.Topic ?? "").Contains(query, StringComparison.OrdinalIgnoreCase) ||
                (m.Fact ?? "").Contains(query, StringComparison.OrdinalIgnoreCase))).ToList();

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

    private static string Snippet(string? fact)
    {
        // N6: a row with a missing fact shows an empty snippet, not a crash.
        var oneLine = (fact ?? "").Trim().ReplaceLineEndings(" ");
        if (oneLine.Length <= 90) return oneLine;
        return oneLine[..89].TrimEnd() + "…";
    }
}
