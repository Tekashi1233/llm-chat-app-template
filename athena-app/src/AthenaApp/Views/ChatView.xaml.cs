using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Threading;
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
    private bool _voiceWired;

    /// <summary>Raised whenever a real message bubble is added, so the shell can switch between the idle wordmark and the conversation view.</summary>
    public event Action? MessagesChanged;

    /// <summary>Raised the moment a live reply from ATHENA lands, so the shell can light the reactor up.</summary>
    public event Action? ReplyReceived;

    /// <summary>How many message bubbles are currently shown.</summary>
    public int MessageCount => MessagesPanel.Children.Count;

    public ChatView(AppState state, Action<string, Color> setStatus)
    {
        _state = state;
        _setStatus = setStatus;
        InitializeComponent();
        _state.ConversationChanged += RenderConversation;
    }

    /// <summary>
    /// N4: refresh the mic + voice-toggle buttons from the live
    /// voice service (called by MainWindow once SAPI is up, and
    /// after any voice setting change). Wires the recognition
    /// callbacks once. When no voice service exists, both buttons
    /// stay disabled — the app never pretends voice works.
    /// </summary>
    public void RefreshVoiceControls()
    {
        var voice = _state.Voice;
        var outputAvailable = voice is not null && voice.IsAvailable;
        VoiceToggleButton.IsEnabled = outputAvailable;
        VoiceToggleButton.Content = _state.VoiceSettings.OutputEnabled ? "Voice: On" : "Voice: Off";
        MicButton.IsEnabled = voice is not null && voice.IsRecognizerAvailable
            && _state.VoiceSettings.MicEnabled;
        if (!_voiceWired && voice is not null)
        {
            _voiceWired = true;
            voice.Recognized += text =>
            {
                InputBox.Text = text;
                InputBox.CaretIndex = text.Length;
                FocusInput();
            };
            voice.ListeningStopped += () =>
            {
                MicButton.Content = "Mic";
                ListeningText.Visibility = Visibility.Collapsed;
            };
        }
    }

    private void MicButton_Click(object sender, RoutedEventArgs e)
    {
        var voice = _state.Voice;
        if (voice is null || !voice.IsRecognizerAvailable)
        {
            AddSystemMessage(
                "Microphone input isn't available on this PC — no speech recognizer or microphone was found.",
                isError: true);
            MicButton.IsEnabled = false;
            return;
        }
        if (!_state.VoiceSettings.MicEnabled)
        {
            AddSystemMessage(
                "Microphone input is off — turn it on in Settings → Voice.",
                isError: false);
            return;
        }
        if (voice.IsListening)
        {
            voice.StopListening();
            return;
        }
        voice.StopSpeaking();
        if (voice.StartListening())
        {
            MicButton.Content = "Stop";
            ListeningText.Visibility = Visibility.Visible;
        }
        else
        {
            AddSystemMessage(
                "Couldn't start listening — check the microphone and try again.",
                isError: true);
        }
    }

    private void VoiceToggleButton_Click(object sender, RoutedEventArgs e)
    {
        var voice = _state.Voice;
        if (voice is null || !voice.IsAvailable)
        {
            AddSystemMessage(
                "Voice output isn't available on this PC — no speech voices were found.",
                isError: true);
            VoiceToggleButton.IsEnabled = false;
            return;
        }
        _state.VoiceSettings = _state.VoiceSettings with
        {
            OutputEnabled = !_state.VoiceSettings.OutputEnabled,
        };
        _state.SaveVoiceSettings();
        if (!_state.VoiceSettings.OutputEnabled)
            voice.StopSpeaking();
        RefreshVoiceControls();
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
            AddSystemMessage("Set your site key first (menu (···) → Settings).", isError: true);
            return;
        }

        _sending = true;
        SendButton.IsEnabled = false;
        InputBox.Text = "";
        AddMessage("You", text.Trim(), isUser: true);
        ThinkingText.Visibility = Visibility.Visible;
        // N4: a new message interrupts any speech in progress.
        _state.Voice?.StopSpeaking();
        _state.SetPresenceState(PresenceState.Thinking, PresenceSource.Chat, "Thinking…");

        try
        {
            var reply = await _state.Chat.SendMessageAsync(
                text, _state.ApiKey, _state.ConversationId);
            AddMessage("ATHENA", reply, isUser: false);
            _state.SetPresenceState(PresenceState.Idle, PresenceSource.Chat, "Idle");
            ReplyReceived?.Invoke();
            // N4: read the reply aloud only when the user turned voice output on.
            if (_state.VoiceSettings.OutputEnabled)
                _state.Voice?.Speak(reply);
        }
        catch (AthenaApiException ex)
        {
            AddSystemMessage(ex.Message, isError: true);
            _state.SetPresenceState(PresenceState.Error, PresenceSource.Chat, ex.Message);
        }
        catch (HttpRequestException)
        {
            AddSystemMessage(
                "Couldn't reach ATHENA — check your connection and try again.",
                isError: true);
            _state.SetPresenceState(PresenceState.Offline, PresenceSource.Chat, "Offline");
        }
        catch (TaskCanceledException)
        {
            AddSystemMessage(
                "The request timed out. Your message was not answered — try again.",
                isError: true);
            _state.SetPresenceState(PresenceState.Offline, PresenceSource.Chat, "Offline — timed out");
        }
        finally
        {
            ThinkingText.Visibility = Visibility.Collapsed;
            SendButton.IsEnabled = true;
            _sending = false;
            InputBox.Focus();
            // If an unexpected error escaped the catches above, never
            // leave the presence stuck on Thinking.
            if (_state.PresenceStatus.State == PresenceState.Thinking)
                _state.SetPresenceState(PresenceState.Idle, PresenceSource.Chat, "Idle");
        }
    }

    private void AddMessage(string who, string text, bool isUser)
    {
        var left = isUser ? 80d : 0d;
        var right = isUser ? 0d : 80d;
        // Frosted glass bubbles: a translucent gradient fill with
        // a thin bright edge and a soft shadow, so a reply can
        // never hide inside a flat dark panel again.
        var bubble = new Border
        {
            Background = (Brush)FindResource("BrushGlassFill"),
            BorderBrush = isUser
                ? (Brush)FindResource("BrushGlassBorder")
                : (Brush)FindResource("BrushGlassGoldBorder"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(10),
            Padding = new Thickness(12, 8, 12, 8),
            Margin = new Thickness(left, 6, right, 6),
            HorizontalAlignment = isUser
                ? HorizontalAlignment.Right
                : HorizontalAlignment.Left,
            Effect = new DropShadowEffect
            {
                Color = Colors.Black,
                BlurRadius = 12,
                ShadowDepth = 2,
                Opacity = 0.35,
            },
        };
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock
        {
            Text = who,
            FontSize = 11,
            FontWeight = FontWeights.SemiBold,
            Foreground = isUser
                ? (Brush)FindResource("BrushMuted")
                : (Brush)FindResource("BrushGold"),
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
        MessagesChanged?.Invoke();
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
        // Scroll AFTER layout: calling ScrollToEnd synchronously
        // runs before the new bubble is measured, so the view stays
        // at the top and the newest reply sits out of sight — the
        // invisible-reply bug. Dispatching to Background priority
        // runs after the layout pass, so the scroll lands at the end.
        if (MessagesPanel.Parent is not ScrollViewer scroller) return;
        Dispatcher.BeginInvoke(
            () => scroller.ScrollToEnd(),
            DispatcherPriority.Background);
    }
}
