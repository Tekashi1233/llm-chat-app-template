using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// The N1 chat window, moved into a view: one conversation, the
/// existing backend. History lives on the server (the Worker loads
/// it per conversation), so a follow-up works exactly as on the
/// website. Kept alive for the whole session by MainWindow so chat
/// state survives navigation. First-run key setup and the
/// connection check stay in MainWindow, not here.
/// </summary>
public partial class ChatView : UserControl, IRefreshable
{
    private readonly AppState _state;
    private readonly Action<string, Color> _setStatus;
    private bool _sending;

    public ChatView(AppState state, Action<string, Color> setStatus)
    {
        _state = state;
        _setStatus = setStatus;
        InitializeComponent();
        _state.ConversationChanged += RenderConversation;
    }

    /// <summary>Chat manages itself; MainWindow does not await this.</summary>
    public Task RefreshAsync(AppState state) => Task.CompletedTask;

    public void FocusInput() => InputBox.Focus();

    /// <summary>
    /// Clears the feed and renders the current conversation's saved
    /// messages (from the backup snapshot) in order. User and
    /// assistant rows become bubbles; anything else is skipped.
    /// </summary>
    public void RenderConversation()
    {
        MessagesPanel.Children.Clear();
        var backup = _state.Backup;
        if (backup is null) return;
        var rows = backup.Messages
            .Where(m => m.ConversationId == _state.ConversationId)
            .OrderBy(m => m.CreatedAtMs);
        foreach (var row in rows)
        {
            if (row.Role == "user")
                AddMessage("You", row.Content, isUser: true);
            else if (row.Role == "assistant")
                AddMessage("ATHENA", row.Content, isUser: false);
        }
        ScrollToBottom();
    }

    private void NewChatButton_Click(object sender, RoutedEventArgs e) =>
        _state.NewConversation();

    private async void SendButton_Click(object sender, RoutedEventArgs e) =>
        await SendAsync();

    private async void InputBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            e.Handled = true;
            await SendAsync();
        }
    }

    private async Task SendAsync()
    {
        if (_sending) return;
        var text = InputBox.Text;
        if (!MessageValidation.IsValidUserMessage(text)) return;
        if (string.IsNullOrWhiteSpace(_state.ApiKey))
        {
            AddSystemMessage("Set your site key first (button above).", isError: true);
            return;
        }

        _sending = true;
        SendButton.IsEnabled = false;
        InputBox.Text = "";
        AddMessage("You", text.Trim(), isUser: true);
        ThinkingText.Visibility = Visibility.Visible;

        try
        {
            var reply = await _state.Chat.SendMessageAsync(
                text, _state.ApiKey, _state.ConversationId);
            AddMessage("ATHENA", reply, isUser: false);
        }
        catch (AthenaApiException ex)
        {
            AddSystemMessage(ex.Message, isError: true);
        }
        catch (HttpRequestException)
        {
            AddSystemMessage(
                "Couldn't reach ATHENA — check your connection and try again.",
                isError: true);
        }
        catch (TaskCanceledException)
        {
            AddSystemMessage(
                "The request timed out. Your message was not answered — try again.",
                isError: true);
        }
        finally
        {
            ThinkingText.Visibility = Visibility.Collapsed;
            SendButton.IsEnabled = true;
            _sending = false;
            InputBox.Focus();
        }
    }

    private void AddMessage(string who, string text, bool isUser)
    {
        var left = isUser ? 80d : 0d;
        var right = isUser ? 0d : 80d;
        var bubble = new Border
        {
            Background = (Brush)(isUser
                ? FindResource("BrushUserBubble")
                : FindResource("BrushPanel")),
            BorderBrush = (Brush)FindResource("BrushGold"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6),
            Padding = new Thickness(12, 8, 12, 8),
            Margin = new Thickness(left, 6, right, 6),
            HorizontalAlignment = isUser
                ? HorizontalAlignment.Right
                : HorizontalAlignment.Left,
        };
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock
        {
            Text = who,
            FontSize = 11,
            FontWeight = FontWeights.SemiBold,
            Foreground = (Brush)FindResource("BrushGold"),
            Margin = new Thickness(0, 0, 0, 4),
        });
        stack.Children.Add(new TextBlock
        {
            Text = text,
            FontSize = 14,
            Foreground = (Brush)FindResource("BrushText"),
            TextWrapping = TextWrapping.Wrap,
        });
        bubble.Child = stack;
        MessagesPanel.Children.Add(bubble);
        ScrollToBottom();
    }

    public void AddSystemMessage(string text, bool isError)
    {
        MessagesPanel.Children.Add(new TextBlock
        {
            Text = text,
            FontSize = 13,
            FontStyle = FontStyles.Italic,
            Foreground = isError
                ? new SolidColorBrush(Color.FromRgb(0xE8, 0x8A, 0x8A))
                : (Brush)FindResource("BrushMuted"),
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 6, 0, 6),
        });
        ScrollToBottom();
    }

    private void ScrollToBottom()
    {
        // The ScrollViewer is the parent of MessagesPanel.
        if (MessagesPanel.Parent is ScrollViewer scroller)
            scroller.ScrollToEnd();
    }
}
