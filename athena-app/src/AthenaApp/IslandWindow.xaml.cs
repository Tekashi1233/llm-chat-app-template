using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Athena.Core;

namespace Athena;

/// <summary>
/// N3 Dynamic Island: a floating native pill that mirrors the
/// app's real state. It never activates itself, never steals
/// focus, and never shows a state no real event produced — every
/// transition arrives through AppState's state machine.
/// Compact: disc + status line. Single click expands to the last
/// reply + actions. Double-click opens the Command Centre.
/// Drag it anywhere; the position is remembered. Right-click for
/// always-on-top / open / hide.
/// </summary>
public partial class IslandWindow : Window
{
    private const double CompactHeight = 56;
    private const double ExpandedHeight = 176;
    private const double WidthPx = 240;

    private readonly AppState _state;
    private readonly string _storeDir;
    private readonly DispatcherTimer _pulseTimer;
    private readonly DispatcherTimer _flashTimer;
    private bool _expanded;
    private bool _pulsing;
    private IslandState _lastState = IslandState.Idle;

    /// <summary>Set by MainWindow when the app is really exiting, so Close() is honoured instead of hiding.</summary>
    public bool AppExiting { get; set; }

    public event Action? OpenCentreRequested;

    public IslandWindow(AppState state, string storeDir)
    {
        _state = state;
        _storeDir = storeDir;
        InitializeComponent();

        _pulseTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(500) };
        _pulseTimer.Tick += (_, _) => Disc.Opacity = Disc.Opacity > 0.6 ? 0.35 : 1.0;

        _flashTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(6) };
        _flashTimer.Tick += (_, _) =>
        {
            _flashTimer.Stop();
            SetExpanded(false);
        };

        _state.IslandStateChanged += OnIslandStateChanged;
        ApplyStatus(_state.IslandStatus);

        var saved = IslandPositionStore.Load(_storeDir);
        if (saved is not null)
        {
            var (left, top) = IslandPositionMath.Clamp(
                saved.Left, saved.Top, WidthPx, CompactHeight,
                SystemParameters.VirtualScreenWidth,
                SystemParameters.VirtualScreenHeight);
            Left = left;
            Top = top;
            Topmost = saved.AlwaysOnTop;
            AlwaysOnTopItem.IsChecked = saved.AlwaysOnTop;
        }
        else
        {
            var def = IslandPositionMath.Default(
                SystemParameters.VirtualScreenWidth,
                SystemParameters.VirtualScreenHeight,
                WidthPx, CompactHeight);
            Left = def.Left;
            Top = def.Top;
            Topmost = def.AlwaysOnTop;
        }
    }

    public void ShowIsland()
    {
        if (!IsVisible) Show();
        Visibility = Visibility.Visible;
        IslandPositionStore.Save(_storeDir, CurrentPosition(visible: true));
    }

    public void HideIsland()
    {
        SetExpanded(false);
        Visibility = Visibility.Hidden;
        IslandPositionStore.Save(_storeDir, CurrentPosition(visible: false));
    }

    public void SavePosition() =>
        IslandPositionStore.Save(_storeDir, CurrentPosition(Visibility == Visibility.Visible));

    private IslandPosition CurrentPosition(bool visible) =>
        new(Left, Top, Topmost, visible);

    private void OnIslandStateChanged(IslandStatus status)
    {
        var wasThinking = _lastState == IslandState.Thinking;
        _lastState = status.State;
        ApplyStatus(status);

        // A reply landed while the Centre is not the active window:
        // flash the Island open briefly with the reply. Never
        // activates or focuses anything — the window only changes
        // its own size and text.
        if (wasThinking && status.State == IslandState.Idle &&
            _state.LastReplySnippet is not null &&
            Application.Current.MainWindow?.IsActive != true)
        {
            SnippetText.Text = _state.LastReplySnippet;
            SetExpanded(true);
            _flashTimer.Stop();
            _flashTimer.Start();
        }
    }

    private void ApplyStatus(IslandStatus status)
    {
        StatusText.Text = status.Detail;
        Disc.Fill = status.State switch
        {
            IslandState.Idle => (Brush)FindResource("BrushGold"),
            IslandState.Thinking => (Brush)FindResource("BrushGold"),
            IslandState.Listening => new SolidColorBrush(Color.FromRgb(0x7F, 0xC8, 0x8A)),
            IslandState.Speaking => new SolidColorBrush(Color.FromRgb(0x7F, 0xC8, 0x8A)),
            IslandState.Working => new SolidColorBrush(Color.FromRgb(0x6F, 0xA8, 0xDC)),
            IslandState.Approval => new SolidColorBrush(Color.FromRgb(0xE8, 0xA3, 0x3D)),
            IslandState.Offline => new SolidColorBrush(Color.FromRgb(0x9A, 0x98, 0x90)),
            IslandState.Error => new SolidColorBrush(Color.FromRgb(0xE8, 0x8A, 0x8A)),
            _ => (Brush)FindResource("BrushGold"),
        };

        // Subtle pulse while thinking; still otherwise.
        if (status.State == IslandState.Thinking)
        {
            if (!_pulsing) { _pulsing = true; _pulseTimer.Start(); }
        }
        else
        {
            _pulsing = false;
            _pulseTimer.Stop();
            Disc.Opacity = 1.0;
        }
    }

    private void SetExpanded(bool expanded)
    {
        _expanded = expanded;
        ExpandedPanel.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        Height = expanded ? ExpandedHeight : CompactHeight;
        PillBorder.CornerRadius = new CornerRadius(expanded ? 16 : 28);
        if (!expanded) _flashTimer.Stop();
    }

    private void PillBorder_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        // Border has no MouseDoubleClick event, so a double-click
        // is detected here via ClickCount.
        if (e.ClickCount == 2)
        {
            OpenCentreRequested?.Invoke();
            return;
        }
        var before = new Point(Left, Top);
        DragMove();
        var moved = Math.Abs(Left - before.X) + Math.Abs(Top - before.Y);
        if (moved < 4)
        {
            // A click, not a drag: toggle the expanded panel.
            if (!_expanded && _state.LastReplySnippet is not null)
                SnippetText.Text = _state.LastReplySnippet;
            SetExpanded(!_expanded);
        }
        else
        {
            SavePosition();
        }
    }

    private void OpenCentreButton_Click(object sender, RoutedEventArgs e) =>
        OpenCentreRequested?.Invoke();

    private void OpenCentreItem_Click(object sender, RoutedEventArgs e) =>
        OpenCentreRequested?.Invoke();

    private void HideButton_Click(object sender, RoutedEventArgs e) => HideIsland();

    private void HideItem_Click(object sender, RoutedEventArgs e) => HideIsland();

    private void AlwaysOnTopItem_Click(object sender, RoutedEventArgs e)
    {
        Topmost = AlwaysOnTopItem.IsChecked;
        SavePosition();
    }

    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        SavePosition();
        if (!AppExiting)
        {
            // The Island is never closed on its own — hiding keeps
            // it available from Settings until the app exits.
            e.Cancel = true;
            HideIsland();
        }
        base.OnClosing(e);
    }
}
