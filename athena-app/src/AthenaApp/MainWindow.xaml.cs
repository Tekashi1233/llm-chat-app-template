using System.IO;
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
    private IslandWindow? _island;

    /// <summary>Same folder as the DPAPI config (%APPDATA%\Athena); the Island's position file lives here.</summary>
    private static string IslandStoreDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Athena");

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
        Closing += MainWindow_Closing;
        _state.ShowIslandRequested += OnShowIslandRequested;
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
        ShowIslandIfEnabled();
        InitialiseVoice();
    }

    /// <summary>
    /// N4: load saved voice preferences and start the SAPI service.
    /// Never throws — a machine without SAPI, voices or a mic just
    /// gets voice controls disabled with an honest explanation.
    /// </summary>
    private void InitialiseVoice()
    {
        _state.VoiceSettingsDir = IslandStoreDir;
        _state.VoiceSettings =
            VoiceSettingsStore.Load(IslandStoreDir)?.Clamped()
            ?? VoiceSettings.Defaults;
        try
        {
            var voice = new VoiceService();
            voice.ApplySettings(_state.VoiceSettings);
            voice.SpeakingStarted += () =>
                _state.SetIslandState(IslandState.Speaking, IslandTriggerSource.Voice, "Speaking…");
            voice.SpeakingFinished += () =>
            {
                if (_state.IslandStatus.State == IslandState.Speaking)
                    _state.SetIslandState(IslandState.Idle, IslandTriggerSource.Voice, "Idle");
            };
            voice.ListeningStarted += () =>
                _state.SetIslandState(IslandState.Listening, IslandTriggerSource.Voice, "Listening…");
            voice.ListeningStopped += () =>
            {
                if (_state.IslandStatus.State == IslandState.Listening)
                    _state.SetIslandState(IslandState.Idle, IslandTriggerSource.Voice, "Idle");
            };
            _state.Voice = voice;
            _chatView.RefreshVoiceControls();
        }
        catch (Exception)
        {
            // Voice stays unavailable; the app works without it.
            _state.Voice = null;
            _chatView.RefreshVoiceControls();
        }
    }

    /// <summary>
    /// N3: create the floating Island once, after the connection
    /// state is known. It shares this window's AppState, so it
    /// shows the same conversation and backend — no second client.
    /// </summary>
    private void ShowIslandIfEnabled()
    {
        _island ??= new IslandWindow(_state, IslandStoreDir);
        _island.OpenCentreRequested -= OnIslandOpenCentre;
        _island.OpenCentreRequested += OnIslandOpenCentre;
        var saved = IslandPositionStore.Load(IslandStoreDir);
        if (saved is null || saved.Visible)
            _island.ShowIsland();
    }

    private void OnShowIslandRequested()
    {
        ShowIslandIfEnabled();
        _island?.ShowIsland();
    }

    private void OnIslandOpenCentre()
    {
        NavigateTo("Chat");
        Activate();
        _chatView.FocusInput();
    }

    private void MainWindow_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        // Stop any speech/recognition first, then save the Island.
        try { _state.Voice?.Dispose(); } catch (Exception) { }
        if (_island is not null)
        {
            _island.SavePosition();
            _island.AppExiting = true;
            _island.Close();
        }
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
                    // Only clear a bad state; the machine refuses a
                    // System Idle while a chat is Thinking.
                    if (_state.IslandStatus.State is IslandState.Offline or IslandState.Error)
                        _state.SetIslandState(IslandState.Idle, IslandTriggerSource.System, "Ready");
                    break;
                case KeyValidationResult.Invalid:
                    SetStatus("Key invalid — use “Set key”", Colors.Red);
                    _chatView.AddSystemMessage(
                        "The stored key was rejected. Please set the correct site key.",
                        isError: true);
                    _state.SetIslandState(IslandState.Error, IslandTriggerSource.System, "Key invalid");
                    break;
                case KeyValidationResult.RateLimited:
                    SetStatus("Rate limited — wait a minute", Colors.Orange);
                    _state.SetIslandState(IslandState.Error, IslandTriggerSource.System, "Rate limited");
                    break;
                default:
                    SetStatus("Could not reach ATHENA", Colors.Orange);
                    _state.SetIslandState(IslandState.Offline, IslandTriggerSource.System, "Offline");
                    break;
            }
        }
        catch (HttpRequestException)
        {
            SetStatus("Offline — check your connection", Colors.Orange);
            _state.SetIslandState(IslandState.Offline, IslandTriggerSource.System, "Offline");
        }
        catch (TaskCanceledException)
        {
            SetStatus("Offline — connection timed out", Colors.Orange);
            _state.SetIslandState(IslandState.Offline, IslandTriggerSource.System, "Offline — timed out");
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
