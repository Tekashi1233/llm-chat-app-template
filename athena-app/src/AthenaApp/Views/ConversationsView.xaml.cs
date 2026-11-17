using System.Windows;
using System.Windows.Controls;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// Conversation list, derived from the /api/backup snapshot:
/// titles come from the first user message, nothing is invented.
/// </summary>
public partial class ConversationsView : UserControl, IRefreshable
{
    private readonly AppState _state;
    private readonly Action<string> _navigate;
    private List<ConversationSummary> _all = new();

    public ConversationsView(AppState state, Action<string> navigate)
    {
        _state = state;
        _navigate = navigate;
        InitializeComponent();
    }

    public async Task RefreshAsync(AppState state)
    {
        ListPanel.Children.Clear();
        ListPanel.Children.Add(ViewHelpers.MutedText("Loading…"));
        try
        {
            if (!state.HasAuth)
            {
                ListPanel.Children.Clear();
                ListPanel.Children.Add(ViewHelpers.MutedText(
                    "Sign in first to see conversations."));
                return;
            }

            state.Backup ??= await state.Data.GetBackupAsync(state.ApiKey, authToken: state.AuthToken);
            _all = state.Backup is null
                ? new List<ConversationSummary>()
                : ConversationStore.GroupMessages(state.Backup.Messages);
            RenderList();
        }
        catch (Exception ex)
        {
            // N6: a failed refresh never leaves a stale list behind
            // — the cached rows are cleared (typing in the search
            // box afterwards must not resurrect them as current)
            // and the panel shows what happened plus what to do next.
            _all = new();
            ListPanel.Children.Clear();
            ListPanel.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
            ListPanel.Children.Add(ViewHelpers.MutedText(ViewHelpers.RetryHintReopen));
        }
    }

    private void SearchBox_TextChanged(object sender, TextChangedEventArgs e) =>
        RenderList();

    private void NewButton_Click(object sender, RoutedEventArgs e)
    {
        _state.NewConversation();
        _navigate("Chat");
    }

    private void RenderList()
    {
        ListPanel.Children.Clear();
        var query = SearchBox?.Text ?? "";
        var shown = ConversationStore.Search(_all, query);
        if (shown.Count == 0)
        {
            ListPanel.Children.Add(ViewHelpers.MutedText(
                _all.Count == 0
                    ? "No conversations yet. Say hello in Chat."
                    : "Nothing matches that search."));
            return;
        }

        foreach (var c in shown)
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
            ListPanel.Children.Add(button);
        }
    }
}
