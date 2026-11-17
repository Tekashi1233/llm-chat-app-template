using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Athena.Core;

namespace Athena;

/// <summary>
/// N1 chat shell: one window, one conversation, the existing backend.
/// History lives on the server (the Worker loads it per conversation),
/// so a follow-up question works exactly as on the website.
/// </summary>
public partial class MainWindow : Window
{
    private readonly ChatClient _client = new();
    private string? _apiKey;
    private string _conversationId = "";
    private bool _sending;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += async (_, _) => await InitialiseAsync();
    }

    private async Task InitialiseAsync()
    {
        SetStatus("Starting…", Colors.Gray);
        try
        {
            _conversationId = SecureStore.GetOrCreateConversationId();
        }
        catch (Exception ex)
        {
            AddSystemMessage(
                "Could not open local settings. ATHENA can still chat once a key is set, " +
                "but this session will not be remembered.\n" + ex.Message,
                isError: true);
            _conversationId = Guid.NewGuid().ToString();
        }

        _apiKey = SecureStore.TryLoadKey();
        if (string.IsNullOrWhiteSpace(_apiKey))
        {
            SetStatus("No key yet", Colors.Gray);
            AddSystemMessage(
                "Welcome to ATHENA. To start chatting, set your site key " +
                "(the same key the website uses). It is stored encrypted " +
                "on this computer only — never in the app itself.",
                isError: false);
            var setup = new SetupWindow(_client);
            if (setup.ShowDialog() == true)
                _apiKey = SecureStore.TryLoadKey();
        }

        if (!string.IsNullOrWhiteSpace(_apiKey))
            await CheckConnectionAsync();
        else
            SetStatus("No key — use “Set key” above", Colors.Gray);

        InputBox.Focus();
    }

    private async Task CheckConnectionAsync()
    {
        SetStatus("Checking connection…", Colors.Gray);
        try
        {
            var result = await _client.ValidateKeyAsync(_apiKey!);
            switch (result)
            {
                case KeyValidationResult.Valid:
                    SetStatus("Connected", Colors.Green);
                    break;
                case KeyValidationResult.Invalid:
                    SetStatus("Key invalid — use “Set key”", Colors.Red);
                    AddSystemMessage(
                        "The stored key was rejected. Please set the correct site key.",
                        isError: true);
                    break;
                case KeyValidationResult.RateLimited:
                    SetStatus("Rate limited — wait a minute", Colors.Orange);
                    break;
                default:
                    SetStatus("Could not reach ATHENA", Colors.Orange);
                    break;
            }
        }
        catch (HttpRequestException)
        {
            SetStatus("Offline — check your connection", Colors.Orange);
        }
        catch (TaskCanceledException)
        {
            SetStatus("Offline — connection timed out", Colors.Orange);
        }
    }

    private void SetStatus(string text, Color dot)
    {
        StatusText.Text = text;
        StatusDot.Fill = new SolidColorBrush(dot);
    }

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
        if (string.IsNullOrWhiteSpace(_apiKey))
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
            var reply = await _client.SendMessageAsync(
                text, _apiKey, _conversationId);
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

    private void AddSystemMessage(string text, bool isError)
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

    private void SetKeyButton_Click(object sender, RoutedEventArgs e)
    {
        var setup = new SetupWindow(_client);
        if (setup.ShowDialog() == true)
        {
            _apiKey = SecureStore.TryLoadKey();
            _ = CheckConnectionAsync();
        }
    }
}
