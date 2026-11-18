using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using Athena.Core;
using Athena.Views;
using WinForms = System.Windows.Forms;

namespace Athena;

/// <summary>
/// Minimal black identity shell: pure black background, ATHENA as
/// an arc reactor in the centre — steel rings, a segmented coil
/// ring and a glowing blue-white core — with subtle red + blue
/// light behind it and a discreet status line. The reactor lights
/// up bright when ATHENA replies, pulses gently while she thinks,
/// and rests dim when idle. The title bar and the chat surfaces
/// are frosted glass (translucent fills, light edges) over the
/// black. No dashboard, no sidebar, no floating Island (N3 is
/// cancelled). The six capability sections (Conversations, Memory
/// Vault, Tools, Devices, Activity, Settings) live behind the
/// "···" menu as an overlay, reusing the existing views. The
/// reactor stays visible during conversations, smaller, at the
/// top — clicking it returns to the idle screen.
/// The reactor lighting follows the presence machine: only states
/// real events produce ever light up. All glow changes are slow
/// (0.8–1.6s) fades — no flashing.
/// First-run key setup, the connection check and the initial data
/// load live here, as in N1. N4 voice is started here too.
/// </summary>
public partial class MainWindow : Window
{
    private readonly AppState _state = new();
    private readonly ChatView _chatView;
    private bool _conversing;
    private string _connectionStatus = "Starting…";
    private DispatcherTimer? _glowTimer;
    private bool _glowSwap;

    // ----- N7 tray + wake word -----
    private WinForms.NotifyIcon? _trayIcon;
    private bool _reallyQuitting;
    private bool _trayBalloonShown;
    private bool _wakeWordPausedForDictation;

    // ----- N8 autostart / --tray -----
    private readonly bool _startHidden;
    private bool _initialised;

    /// <summary>Same folder as the DPAPI config (%APPDATA%\Athena); voice.json lives here too.</summary>
    private static string AppDataDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Athena");

    public MainWindow() : this(StartupOptions.Default)
    {
    }

    /// <summary>
    /// N8: with --tray the window starts hidden — the tray icon is
    /// live from launch and initialisation runs immediately (the
    /// Loaded event only fires when the window is first shown, so
    /// a hidden window would otherwise never initialise). Whether
    /// the wake-word listener starts is decided by the persisted
    /// setting, not by --tray: CreateWakeWordService only starts
    /// it when WakeWordEnabled is on, so a --tray launch with the
    /// wake word off sits quietly in the tray and does not listen.
    /// </summary>
    public MainWindow(StartupOptions options)
    {
        _startHidden = options.StartHidden;
        InitializeComponent();
        _chatView = new ChatView(_state, SetStatus);
        ChatArea.Content = _chatView;
        _chatView.MessagesChanged += UpdateLayoutFromMessages;
        _chatView.ReplyReceived += FlashReactor;
        _state.ConversationChanged += () => UpdateLayoutFromMessages();
        _state.PresenceChanged += ApplyPresence;
        _state.WakeWordSettingChanged += OnWakeWordSettingChanged;
        Loaded += async (_, _) => await InitialiseOnceAsync();
        Closing += MainWindow_Closing;
        Closed += MainWindow_Closed;
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape && Overlay.Visibility == Visibility.Visible)
            {
                CloseOverlay();
                e.Handled = true;
            }
        };
        if (_startHidden)
        {
            // N8: hide before the window is ever shown (App only
            // calls Show() on a normal launch), make the tray live,
            // and initialise now — Loaded only fires when the window
            // is first shown, so a hidden window would otherwise
            // never initialise. _chatView exists by now, so
            // InitialiseAsync is safe to run.
            Visibility = Visibility.Hidden;
            EnsureTrayIcon();
            if (_trayIcon is not null)
            {
                _trayBalloonShown = true;
                try
                {
                    // Neutral wording on purpose: the wake-word
                    // setting is not loaded yet, so this must not
                    // claim listening when the wake word is off.
                    _trayIcon.ShowBalloonTip(3000, "ATHENA",
                        "ATHENA started with Windows and is running in the tray — double-click the tray icon to open it",
                        WinForms.ToolTipIcon.Info);
                }
                catch (Exception) { }
            }
            _ = InitialiseOnceAsync();
        }
    }

    /// <summary>
    /// N8: initialisation runs exactly once — either from the
    /// constructor on a --tray launch (hidden window, no Loaded
    /// yet) or from Loaded on a normal launch. When the window is
    /// shown later after a --tray start, Loaded fires again and
    /// this is a no-op.
    /// </summary>
    private async Task InitialiseOnceAsync()
    {
        if (_initialised) return;
        _initialised = true;
        await InitialiseAsync();
    }

    /// <summary>
    /// N8: reconcile the Run key with the autostart setting. Cleans
    /// a stale entry first (exe path no longer exists), then
    /// registers the key when the setting is on or removes it when
    /// off. Best effort — a registry failure never breaks startup.
    /// The policy only writes when the entry is missing or wrong;
    /// it never re-registers blindly every launch.
    /// </summary>
    private void ApplyAutostartSetting()
    {
        try
        {
            var store = new WindowsAutostartStore();
            AutostartPolicy.CleanStaleEntry(store, File.Exists);
            var exePath = Environment.ProcessPath
                ?? Process.GetCurrentProcess().MainModule?.FileName;
            if (string.IsNullOrWhiteSpace(exePath)) return;
            if (_state.AppSettings.AutostartEnabled)
                AutostartPolicy.EnsureEnabled(store, exePath);
            else
                AutostartPolicy.EnsureDisabled(store);
        }
        catch (Exception)
        {
            // Autostart is a nicety; startup must not fail over it.
        }
    }

    private async Task InitialiseAsync()
    {
        SetStatus("Starting…", Colors.Gray);
        // N8: app settings (settings.json) + autostart. Defaults
        // are autostart ON — the first run of this build registers
        // the Run key, which is the approved behaviour.
        _state.AppSettingsDir = AppDataDir;
        _state.AppSettings = AppSettingsStore.Load(AppDataDir) ?? AppSettings.Defaults;
        ApplyAutostartSetting();
        // The N3 Island is cancelled: drop its leftover position file.
        try { File.Delete(Path.Combine(AppDataDir, "island.json")); } catch (Exception) { }
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

        _state.AuthToken = SecureStore.TryLoadToken();
        _state.ApiKey = SecureStore.TryLoadKey();

        // N5 M2 migration for existing installs: a stored site key is
        // exchanged once for a session token, then deleted from the
        // config. If the exchange fails (auth endpoints not deployed
        // yet, or the key is bad) the key stays and the app keeps
        // working on it through the grace period.
        if (AuthValidation.ShouldMigrate(
                !string.IsNullOrWhiteSpace(_state.AuthToken),
                !string.IsNullOrWhiteSpace(_state.ApiKey)))
        {
            try
            {
                var exchanged = await _state.Auth.ExchangeAsync(_state.ApiKey!);
                SecureStore.SaveToken(exchanged.Token);
                SecureStore.DeleteKey();
                _state.AuthToken = exchanged.Token;
                _state.ApiKey = null;
            }
            catch (Exception)
            {
                // Grace period: keep the key, try again next launch.
            }
        }

        if (!_state.HasAuth)
        {
            // N8: a --tray launch with no sign-in cannot stay hidden
            // — the setup dialog would pop up over nothing. Show the
            // window so the user can sign in, then carry on.
            if (_startHidden)
                ShowWindowForWakeWord();
            SetStatus("Not signed in", Colors.Gray);
            _chatView.AddSystemMessage(
                "Welcome to ATHENA. To start chatting, sign in with your " +
                "site key (menu (···) at the top right, then Settings). " +
                "Your key is stored encrypted on this computer only.",
                isError: false);
            var setup = new SetupWindow(_state.Chat, _state.Auth);
            if (setup.ShowDialog() == true)
            {
                _state.AuthToken = SecureStore.TryLoadToken();
                _state.ApiKey = SecureStore.TryLoadKey();
            }
        }

        if (_state.HasAuth)
            await CheckConnectionAsync();
        else
            SetStatus("Not signed in — menu (···) → Settings to sign in", Colors.Gray);

        await LoadDataAsync();
        _chatView.FocusInput();
        // N7: render history at Background priority so the input is
        // live first — a long history must not delay typing.
        _ = Dispatcher.InvokeAsync(_chatView.RenderConversation, DispatcherPriority.Background);
        // N7: SAPI init can be slow — defer it past first paint so the
        // window appears instantly. InitialiseVoice itself is unchanged.
        _ = Dispatcher.InvokeAsync(InitialiseVoice, DispatcherPriority.Background);
    }

    /// <summary>
    /// N4: load saved voice preferences and start the SAPI service.
    /// Never throws — a machine without SAPI, voices or a mic just
    /// gets voice controls disabled with an honest explanation.
    /// </summary>
    private void InitialiseVoice()
    {
        _state.VoiceSettingsDir = AppDataDir;
        _state.VoiceSettings =
            VoiceSettingsStore.Load(AppDataDir)?.Clamped()
            ?? VoiceSettings.Defaults;
        try
        {
            var voice = new VoiceService();
            voice.ApplySettings(_state.VoiceSettings);
            voice.SpeakingStarted += () =>
                _state.SetPresenceState(PresenceState.Speaking, PresenceSource.Voice, "Speaking…");
            voice.SpeakingFinished += () =>
            {
                if (_state.PresenceStatus.State == PresenceState.Speaking)
                    _state.SetPresenceState(PresenceState.Idle, PresenceSource.Voice, "Idle");
            };
            voice.ListeningStarted += () =>
                _state.SetPresenceState(PresenceState.Listening, PresenceSource.Voice, "Listening…");
            voice.ListeningStopped += () =>
            {
                if (_state.PresenceStatus.State == PresenceState.Listening)
                    _state.SetPresenceState(PresenceState.Idle, PresenceSource.Voice, "Idle");
            };
            // N7: dictation and the wake-word listener share one mic,
            // so they never run at once — the mic button pauses the
            // wake word, and it resumes when dictation stops.
            voice.ListeningStarted += PauseWakeWordForDictation;
            voice.ListeningStopped += ResumeWakeWordAfterDictation;
            _state.Voice = voice;
            _chatView.RefreshVoiceControls();
        }
        catch (Exception)
        {
            // Voice stays unavailable; the app works without it.
            _state.Voice = null;
            _chatView.RefreshVoiceControls();
        }

        // N7: the wake word is independent of dictation/TTS — its own
        // try/catch, so a voice failure never kills it and vice versa.
        CreateWakeWordService();
    }

    /// <summary>
    /// N7: build the wake-word listener (its own SAPI engine) and
    /// start it when the setting is on. Never throws — a machine
    /// without SAPI or a mic just gets no wake word, and the app
    /// works without it.
    /// </summary>
    private void CreateWakeWordService()
    {
        try
        {
            if (_state.WakeWord is not null) return;
            var wake = new WakeWordService();
            wake.WakeWordHeard += ShowWindowForWakeWord;
            _state.WakeWord = wake;
            if (_state.VoiceSettings.WakeWordEnabled)
                wake.Start();
        }
        catch (Exception)
        {
            // Wake word stays unavailable; the app works without it.
            _state.WakeWord = null;
        }
    }

    /// <summary>N7: the Settings toggle flipped — start or stop the listener live, no restart needed.</summary>
    private void OnWakeWordSettingChanged()
    {
        if (_state.VoiceSettings.WakeWordEnabled)
        {
            CreateWakeWordService();
            TryStartWakeWordWithRetry();
        }
        else
        {
            _state.WakeWord?.Stop();
        }
    }

    /// <summary>N7: start the listener; one retry a second later if the mic is still busy winding down from dictation.</summary>
    private void TryStartWakeWordWithRetry()
    {
        if (!_state.VoiceSettings.WakeWordEnabled) return;
        if (_state.WakeWord?.Start() != false) return;
        var retry = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        retry.Tick += (_, _) =>
        {
            retry.Stop();
            if (_state.VoiceSettings.WakeWordEnabled)
                _state.WakeWord?.Start();
        };
        retry.Start();
    }

    private void PauseWakeWordForDictation()
    {
        if (_state.WakeWord is { IsListening: true })
        {
            _wakeWordPausedForDictation = true;
            _state.WakeWord.Stop();
        }
    }

    private void ResumeWakeWordAfterDictation()
    {
        if (!_wakeWordPausedForDictation) return;
        _wakeWordPausedForDictation = false;
        TryStartWakeWordWithRetry();
    }

    /// <summary>
    /// N7: the wake word only opens the interface — it never sends
    /// anything. If the presence machine refuses Listening (e.g. a
    /// chat is Thinking — only Chat may clear that), the window
    /// still shows and takes focus; the state just stays as it was.
    /// </summary>
    private void ShowWindowForWakeWord()
    {
        if (!IsVisible)
            Show();
        if (WindowState == WindowState.Minimized)
            WindowState = WindowState.Normal;
        Activate();
        // Topmost flick: forces the window to the front even when
        // another app has focus, then hands topmost back.
        Topmost = true;
        Topmost = false;
        _state.SetPresenceState(PresenceState.Listening, PresenceSource.Voice, "Listening…");
        _chatView.FocusInput();
    }

    // ----- Tray-resident window (N7) -----
    // Closing the window hides it to the tray instead of quitting:
    // the window stays loaded, so "athena" (or the tray menu)
    // brings it back instantly. Real quit is only from the tray
    // menu. Minimizing stays minimizing — only Close hides to tray.

    private void EnsureTrayIcon()
    {
        if (_trayIcon is not null) return;
        try
        {
            // Icon straight from the exe (its ApplicationIcon,
            // icon.ico) — no extra file to ship, and it survives
            // single-file publish. If it cannot be read, there is
            // no tray icon and closing falls through to a real quit.
            System.Drawing.Icon? icon = null;
            try
            {
                var exePath = Environment.ProcessPath
                    ?? Process.GetCurrentProcess().MainModule?.FileName;
                if (!string.IsNullOrWhiteSpace(exePath))
                    icon = System.Drawing.Icon.ExtractAssociatedIcon(exePath);
            }
            catch (Exception) { }
            if (icon is null) return;
            var menu = new WinForms.ContextMenuStrip();
            var showItem = new WinForms.ToolStripMenuItem("Show ATHENA");
            showItem.Click += (_, _) => ShowWindowForWakeWord();
            var quitItem = new WinForms.ToolStripMenuItem("Quit");
            quitItem.Click += (_, _) => QuitFromTray();
            menu.Items.Add(showItem);
            menu.Items.Add(quitItem);
            _trayIcon = new WinForms.NotifyIcon
            {
                Icon = icon,
                Text = "ATHENA",
                Visible = true,
                ContextMenuStrip = menu,
            };
            _trayIcon.DoubleClick += (_, _) => ShowWindowForWakeWord();
        }
        catch (Exception)
        {
            _trayIcon = null;
        }
    }

    private void QuitFromTray()
    {
        _reallyQuitting = true;
        Close();
    }

    private void DisposeTrayIcon()
    {
        if (_trayIcon is null) return;
        try { _trayIcon.Visible = false; } catch (Exception) { }
        try { _trayIcon.Dispose(); } catch (Exception) { }
        _trayIcon = null;
    }

    private void MainWindow_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        if (!_reallyQuitting)
        {
            EnsureTrayIcon();
            if (_trayIcon is not null)
            {
                e.Cancel = true;
                Hide();
                if (!_trayBalloonShown)
                {
                    _trayBalloonShown = true;
                    try
                    {
                        _trayIcon.ShowBalloonTip(3000, "ATHENA",
                            "ATHENA is still listening in the tray — say 'athena' to bring it back",
                            WinForms.ToolTipIcon.Info);
                    }
                    catch (Exception) { }
                }
                return;
            }
            // No tray icon available: fall through to a real quit.
            _reallyQuitting = true;
        }
        // Real quit: stop speech/recognition first.
        try { _state.Voice?.Dispose(); } catch (Exception) { }
        try { _state.WakeWord?.Dispose(); } catch (Exception) { }
        DisposeTrayIcon();
        StopGlowTimer();
    }

    private void MainWindow_Closed(object? sender, EventArgs e)
    {
        // ShutdownMode is OnExplicitShutdown (App.xaml), so the app
        // only exits here — hiding the last window must not quit it.
        if (_reallyQuitting)
            Application.Current.Shutdown();
    }

    /// <summary>Re-validates the auth and reloads model info + backup (used after a sign-in change).</summary>
    public async Task ReconnectAsync()
    {
        _state.AuthToken = SecureStore.TryLoadToken();
        _state.ApiKey = SecureStore.TryLoadKey();
        if (_state.HasAuth)
        {
            await CheckConnectionAsync();
            await LoadDataAsync();
            _chatView.RenderConversation();
        }
        else
        {
            SetStatus("Not signed in — menu (···) → Settings to sign in", Colors.Gray);
        }
    }

    private async Task LoadDataAsync()
    {
        if (!_state.HasAuth) return;
        // Best effort: views surface their own errors if these fail.
        // N7: the two calls are independent — start both at once so
        // readiness pays one round-trip, not two back-to-back.
        // Each await still swallows its own failure (best effort).
        var modelInfoTask = _state.Data.GetModelInfoAsync();
        var backupTask = _state.Data.GetBackupAsync(
            _state.ApiKey, authToken: _state.AuthToken);
        try
        {
            _state.ModelInfo = await modelInfoTask;
        }
        catch (Exception)
        {
            // Left null; views show "couldn't read model info".
        }
        try
        {
            _state.Backup = await backupTask;
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
            var result = await _state.Chat.ValidateAuthAsync(
                _state.AuthToken, _state.ApiKey);
            switch (result)
            {
                case KeyValidationResult.Valid:
                    SetStatus("Connected", Colors.Green);
                    // Only clear a bad state; the machine refuses a
                    // System Idle while a chat is Thinking.
                    if (_state.PresenceStatus.State is PresenceState.Offline or PresenceState.Error)
                        _state.SetPresenceState(PresenceState.Idle, PresenceSource.System, "Ready");
                    break;
                case KeyValidationResult.Invalid:
                    SetStatus("Sign-in expired — menu (···) → Settings to sign in again", Colors.Red);
                    _chatView.AddSystemMessage(
                        "Your session was rejected. Please sign in again.",
                        isError: true);
                    _state.SetPresenceState(PresenceState.Error, PresenceSource.System, "Not signed in");
                    break;
                case KeyValidationResult.RateLimited:
                    SetStatus("Rate limited — wait a minute", Colors.Orange);
                    _state.SetPresenceState(PresenceState.Error, PresenceSource.System, "Rate limited");
                    break;
                default:
                    SetStatus("Could not reach ATHENA", Colors.Orange);
                    _state.SetPresenceState(PresenceState.Offline, PresenceSource.System, "Offline");
                    break;
            }
        }
        catch (HttpRequestException)
        {
            SetStatus("Offline — check your connection", Colors.Orange);
            _state.SetPresenceState(PresenceState.Offline, PresenceSource.System, "Offline");
        }
        catch (TaskCanceledException)
        {
            SetStatus("Offline — connection timed out", Colors.Orange);
            _state.SetPresenceState(PresenceState.Offline, PresenceSource.System, "Offline — timed out");
        }
    }

    /// <summary>Discreet status line under the wordmark. Also remembers the connection status for when the presence returns to Idle.</summary>
    public void SetStatus(string text, Color color)
    {
        _connectionStatus = text;
        StatusText.Text = text;
        StatusText.Foreground = new SolidColorBrush(color);
    }

    // ----- Layout: idle wordmark vs conversation -----

    private void UpdateLayoutFromMessages()
    {
        if (_chatView.MessageCount > 0)
            SetConversationLayout();
        else
            SetIdleLayout();
    }

    private void SetIdleLayout()
    {
        if (!_conversing && WordmarkRow.Height.IsStar) return;
        _conversing = false;
        WordmarkRow.Height = new GridLength(1, GridUnitType.Star);
        ChatRow.Height = GridLength.Auto;
        AnimateReactorSize(260);
        AnimateFontSize(28);
        WordmarkText.Cursor = Cursors.Arrow;
        WordmarkText.ToolTip = "ATHENA";
    }

    private void SetConversationLayout()
    {
        if (_conversing) return;
        _conversing = true;
        WordmarkRow.Height = GridLength.Auto;
        ChatRow.Height = new GridLength(1, GridUnitType.Star);
        AnimateReactorSize(84);
        AnimateFontSize(16);
        WordmarkText.Cursor = Cursors.Hand;
        WordmarkText.ToolTip = "Click to return to the idle screen";
    }

    private void WordmarkText_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (!_conversing) return;
        _state.NewConversation();
        SetIdleLayout();
        _chatView.FocusInput();
    }

    private void Reactor_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) =>
        WordmarkText_MouseLeftButtonDown(sender, e);

    private void AnimateReactorSize(double size)
    {
        var ease = new CubicEase { EasingMode = EasingMode.EaseInOut };
        var duration = TimeSpan.FromSeconds(0.6);
        Reactor.BeginAnimation(FrameworkElement.WidthProperty,
            new DoubleAnimation(size, duration) { EasingFunction = ease });
        Reactor.BeginAnimation(FrameworkElement.HeightProperty,
            new DoubleAnimation(size, duration) { EasingFunction = ease });
    }

    /// <summary>
    /// Reply flash: the reactor lights up bright the moment ATHENA
    /// answers, then settles back to whatever the presence says.
    /// A slow ease, not a strobe.
    /// </summary>
    public void FlashReactor()
    {
        SetReactor(0.95, 1.0, 0.3);
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2.0) };
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            ApplyPresence(_state.PresenceStatus);
        };
        timer.Start();
    }

    private void SetReactor(double glow, double core, double seconds = 0.8)
    {
        AnimateOpacity(ReactorGlow, glow, seconds);
        AnimateOpacity(ReactorCore, core, seconds);
    }

    private void AnimateFontSize(double target)
    {
        WordmarkText.BeginAnimation(
            TextBlock.FontSizeProperty,
            new DoubleAnimation(target, TimeSpan.FromSeconds(0.6))
            {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut },
            });
    }

    // ----- Presence → wordmark lighting -----

    private void ApplyPresence(PresenceStatus status)
    {
        // The status line shows what ATHENA is doing, except at Idle,
        // where it goes back to the connection status.
        if (status.State == PresenceState.Idle)
        {
            StatusText.Text = _connectionStatus;
            StatusText.Foreground = (Brush)FindResource("BrushMuted");
        }
        else
        {
            StatusText.Text = status.Detail;
            StatusText.Foreground = (Brush)FindResource("BrushMuted");
        }

        StopGlowTimer();
        switch (status.State)
        {
            case PresenceState.Idle:
                AnimateOpacity(BlueGlow, 0.16);
                AnimateOpacity(RedGlow, 0.16);
                SetReactor(0.25, 0.55);
                break;
            case PresenceState.Listening:
                AnimateOpacity(BlueGlow, 0.45);
                AnimateOpacity(RedGlow, 0.16);
                SetReactor(0.60, 0.75);
                break;
            case PresenceState.Thinking:
                // Slow cross-fade: blue and red trade prominence
                // every 1.2s, and the reactor breathes with them.
                // Gentle, never flashing.
                _glowSwap = false;
                StartGlowTimer(TimeSpan.FromSeconds(1.2), () =>
                {
                    _glowSwap = !_glowSwap;
                    AnimateOpacity(BlueGlow, _glowSwap ? 0.14 : 0.42, 1.2);
                    AnimateOpacity(RedGlow, _glowSwap ? 0.42 : 0.14, 1.2);
                    SetReactor(_glowSwap ? 0.35 : 0.60, 0.65, 1.2);
                });
                break;
            case PresenceState.Speaking:
                // Slow pulse on the blue glow while speech plays,
                // and the reactor pulses with it.
                // An activity indicator, not a waveform.
                _glowSwap = false;
                StartGlowTimer(TimeSpan.FromSeconds(1.6), () =>
                {
                    _glowSwap = !_glowSwap;
                    AnimateOpacity(BlueGlow, _glowSwap ? 0.50 : 0.30, 1.6);
                    AnimateOpacity(RedGlow, 0.16, 1.6);
                    SetReactor(_glowSwap ? 0.85 : 0.50, 0.85, 1.6);
                });
                break;
            case PresenceState.Working:
            case PresenceState.Approval:
                AnimateOpacity(BlueGlow, 0.30);
                AnimateOpacity(RedGlow, 0.30);
                SetReactor(0.45, 0.70);
                break;
            case PresenceState.Offline:
                AnimateOpacity(BlueGlow, 0.07);
                AnimateOpacity(RedGlow, 0.07);
                SetReactor(0.08, 0.15);
                break;
            case PresenceState.Error:
                AnimateOpacity(BlueGlow, 0.10);
                AnimateOpacity(RedGlow, 0.35);
                SetReactor(0.10, 0.25);
                break;
        }
    }

    private static void AnimateOpacity(UIElement element, double target, double seconds = 0.8)
    {
        element.BeginAnimation(
            UIElement.OpacityProperty,
            new DoubleAnimation(target, TimeSpan.FromSeconds(seconds))
            {
                EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut },
            });
    }

    private void StartGlowTimer(TimeSpan interval, Action tick)
    {
        StopGlowTimer();
        _glowTimer = new DispatcherTimer { Interval = interval };
        _glowTimer.Tick += (_, _) => tick();
        _glowTimer.Start();
        tick();
    }

    private void StopGlowTimer()
    {
        if (_glowTimer is null) return;
        _glowTimer.Stop();
        _glowTimer = null;
    }

    // ----- Glass title bar (custom chrome) -----
    // Dragging and double-click-to-maximize are handled by the OS
    // caption (WindowChrome CaptionHeight 34); these are only the
    // three caption buttons.

    private void MinimizeButton_Click(object sender, RoutedEventArgs e) =>
        WindowState = WindowState.Minimized;

    private void MaximizeButton_Click(object sender, RoutedEventArgs e) =>
        WindowState = WindowState == WindowState.Maximized
            ? WindowState.Normal
            : WindowState.Maximized;

    private void CloseButton_Click(object sender, RoutedEventArgs e) => Close();

    // ----- Minimal menu + overlay sections -----

    private void MenuButton_Click(object sender, RoutedEventArgs e) => ShowMenu();

    private void ShowMenu()
    {
        OverlayTitle.Text = "Menu";
        OverlayBackButton.Visibility = Visibility.Collapsed;
        var panel = new StackPanel();
        foreach (var section in new[] { "Conversations", "Memory Vault", "Tools", "Devices", "Activity", "Settings" })
        {
            var name = section;
            var button = new Button
            {
                Content = name,
                Background = Brushes.Transparent,
                Foreground = (Brush)FindResource("BrushText"),
                FontSize = 15,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(12, 10, 12, 10),
                Margin = new Thickness(0, 2, 0, 2),
                BorderThickness = new Thickness(0),
            };
            button.Click += (_, _) => ShowSection(name);
            panel.Children.Add(button);
        }
        OverlayContent.Content = panel;
        Overlay.Visibility = Visibility.Visible;
    }

    private async void ShowSection(string section)
    {
        OverlayTitle.Text = section;
        OverlayBackButton.Visibility = Visibility.Visible;
        UserControl view = section switch
        {
            "Conversations" => new ConversationsView(_state, _ => CloseOverlay()),
            "Memory Vault" => new MemoryVaultView(_state),
            "Tools" => new ToolsView(_state),
            "Devices" => new DevicesView(_state, SendDeviceCommandAsync),
            "Activity" => new ActivityView(_state),
            "Settings" => new SettingsView(_state, async () => await ReconnectAsync()),
            _ => new ToolsView(_state),
        };
        OverlayContent.Content = view;
        Overlay.Visibility = Visibility.Visible;
        if (view is IRefreshable refreshable)
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

    /// <summary>
    /// M1: a device command tapped in the Devices view: close the
    /// overlay so the user sees the chat, then send it through
    /// chat (the model routes it to the device tools).
    /// </summary>
    private async Task SendDeviceCommandAsync(string prompt)
    {
        CloseOverlay();
        await _chatView.SendDeviceCommandAsync(prompt);
    }

    private void OverlayBackButton_Click(object sender, RoutedEventArgs e) => ShowMenu();

    private void CloseOverlay_Click(object sender, RoutedEventArgs e) => CloseOverlay();

    private void Overlay_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        // Clicking the dim area (not the panel) closes the overlay.
        if (e.Source == Overlay)
            CloseOverlay();
    }

    private void CloseOverlay() => Overlay.Visibility = Visibility.Collapsed;
}
