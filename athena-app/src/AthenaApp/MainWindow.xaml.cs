using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Athena.Core;
using Athena.Views;

namespace Athena;

/// <summary>
/// N2 Command Centre shell: sidebar navigation, top bar with
/// connection status, and one content area. The single ChatView
/// instance lives for the whole session so chat state survives
/// navigation; every other view is created fresh per visit.
/// First-run key setup, the connection check and the initial
/// data load live here, as in N1.
/// </summary>
public partial class MainWindow : Window
{
    private readonly AppState _state = new();
    private readonly ChatView _chatView;
    private readonly List<Button> _navButtons = new();

    public MainWindow()
    {
        InitializeComponent();
        _chatView = new ChatView(_state, SetStatus);
        foreach (var child in NavPanel.Children)
        {
            if (child is Button button)
                _navButtons.Add(button);
        }
        Loaded += async (_, _) => await InitialiseAsync();
        NavigateTo("Chat");
    }

    private async Task InitialiseAsync()
    {
        SetStatus("Starting…", Colors.Gray);
        try
        {
            _state.ConversationId = SecureStore.GetOrCreateConversationId();
        }
        catch (Exception ex)
        {
            _chatView.AddSystemMessage(
                "Could not open local settings. ATHENA can still chat once a key is set, " +
                "but this session will not be remembered.\n" + ex.Message,
                isError: true);
            _state.ConversationId = Guid.NewGuid().ToString();
        }

        _state.ApiKey = SecureStore.TryLoadKey();
        if (string.IsNullOrWhiteSpace(_state.ApiKey))
        {
            SetStatus("No key yet", Colors.Gray);
            _chatView.AddSystemMessage(
                "Welcome to ATHENA. To start chatting, set your site key " +
                "(the same key the website uses). It is stored encrypted " +
                "on this computer only — never in the app itself.",
                isError: false);
            var setup = new SetupWindow(_state.Chat);
            if (setup.ShowDialog() == true)
                _state.ApiKey = SecureStore.TryLoadKey();
        }

        if (!string.IsNullOrWhiteSpace(_state.ApiKey))
            await CheckConnectionAsync();
        else
            SetStatus("No key — use “Set key” above", Colors.Gray);

        await LoadDataAsync();
        _chatView.RenderConversation();
        _chatView.FocusInput();
    }

    /// <summary>Re-validates the key and reloads model info + backup (used after a key change).</summary>
    public async Task ReconnectAsync()
    {
        if (!string.IsNullOrWhiteSpace(_state.ApiKey))
        {
            await CheckConnectionAsync();
            await LoadDataAsync();
            _chatView.RenderConversation();
        }
        else
        {
            SetStatus("No key — use “Set key” above", Colors.Gray);
        }
    }

    private async Task LoadDataAsync()
    {
        if (string.IsNullOrWhiteSpace(_state.ApiKey)) return;
        // Best effort: views surface their own errors if these fail.
        try
        {
            _state.ModelInfo = await _state.Data.GetModelInfoAsync();
        }
        catch (Exception)
        {
            // Left null; views show "couldn't read model info".
        }
        try
        {
            _state.Backup = await _state.Data.GetBackupAsync(_state.ApiKey);
        }
        catch (Exception)
        {
            // Left null; views fetch again and show their own errors.
        }
    }

    private async Task CheckConnectionAsync()
    {
        SetStatus("Checking connection…", Colors.Gray);
        try
        {
            var result = await _state.Chat.ValidateKeyAsync(_state.ApiKey!);
            switch (result)
            {
                case KeyValidationResult.Valid:
                    SetStatus("Connected", Colors.Green);
                    break;
                case KeyValidationResult.Invalid:
                    SetStatus("Key invalid — use “Set key”", Colors.Red);
                    _chatView.AddSystemMessage(
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

    public void SetStatus(string text, Color dot)
    {
        StatusText.Text = text;
        StatusDot.Fill = new SolidColorBrush(dot);
    }

    /// <summary>Switches the content area. Called by sidebar buttons and by views (e.g. open a conversation in Chat).</summary>
    public async void NavigateTo(string section)
    {
        foreach (var button in _navButtons)
        {
            var active = (string?)button.Tag == section;
            button.Foreground = (Brush)(active
                ? FindResource("BrushGold")
                : FindResource("BrushMuted"));
            button.BorderBrush = (Brush)(active
                ? FindResource("BrushGold")
                : Brushes.Transparent);
        }

        UserControl view = section switch
        {
            "Dashboard" => new DashboardView(_state, NavigateTo),
            "Chat" => _chatView,
            "Conversations" => new ConversationsView(_state, NavigateTo),
            "Memory Vault" => new MemoryVaultView(_state),
            "Tools" => new ToolsView(_state),
            "Devices" => new DevicesView(_state),
            "Activity" => new ActivityView(_state),
            "Settings" => new SettingsView(_state, async () => await ReconnectAsync()),
            _ => _chatView,
        };
        ContentArea.Content = view;

        // Chat manages itself; every other view reloads on visit.
        if (view is IRefreshable refreshable && view is not ChatView)
        {
            try
            {
                await refreshable.RefreshAsync(_state);
            }
            catch (Exception)
            {
                // Views show their own loading/error states inline.
            }
        }
    }

    private void NavButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is string section)
            NavigateTo(section);
    }

    private async void SetKeyButton_Click(object sender, RoutedEventArgs e)
    {
        var setup = new SetupWindow(_state.Chat);
        if (setup.ShowDialog() == true)
        {
            _state.ApiKey = SecureStore.TryLoadKey();
            await ReconnectAsync();
        }
    }
}
