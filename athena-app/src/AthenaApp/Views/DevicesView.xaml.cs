using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// Paired Windows laptop, from the Companion heartbeat via the
/// N2 device-status endpoint. Read-only: pairing and permissions
/// stay in the Companion tray app on the laptop.
/// </summary>
public partial class DevicesView : UserControl, IRefreshable
{
    private readonly AppState _state;
    private readonly Func<string, Task>? _sendDeviceCommand;

    /// <summary>
    /// sendDeviceCommand runs one approved device command through
    /// chat (MainWindow closes the overlay and sends it). Null in
    /// tests — the quick-action buttons stay disabled then.
    /// </summary>
    public DevicesView(AppState state, Func<string, Task>? sendDeviceCommand = null)
    {
        _state = state;
        _sendDeviceCommand = sendDeviceCommand;
        InitializeComponent();
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e) =>
        await RefreshAsync(_state);

    public async Task RefreshAsync(AppState state)
    {
        ContentPanel.Children.Clear();
        ContentPanel.Children.Add(ViewHelpers.MutedText("Loading…"));
        try
        {
            if (string.IsNullOrWhiteSpace(state.ApiKey))
            {
                ContentPanel.Children.Clear();
                ContentPanel.Children.Add(ViewHelpers.MutedText(
                    "Set your site key first to see device status."));
                return;
            }

            var status = await state.Data.GetDeviceStatusAsync(state.ApiKey);
            ContentPanel.Children.Clear();
            if (status is null)
            {
                ContentPanel.Children.Add(ViewHelpers.MutedText(ViewHelpers.NotEnabledDevice));
                return;
            }

            var card = ViewHelpers.Card(status.Device ?? "Paired laptop", out var body);
            body.Children.Add(ViewHelpers.StatusLine(
                status.Online ? "Online" : "Offline",
                status.Online ? Colors.Green : Colors.Red));
            if (!status.Online && status.Reason is not null)
                body.Children.Add(ViewHelpers.BodyText("Reason: " + status.Reason));
            if (status.CpuPercent is not null)
                body.Children.Add(ViewHelpers.BodyText($"CPU: {status.CpuPercent.Value:F1}%"));
            if (status.RamUsedGb is not null && status.RamTotalGb is not null)
                body.Children.Add(ViewHelpers.BodyText(
                    $"RAM: {status.RamUsedGb.Value:F1} GB used of {status.RamTotalGb.Value:F1} GB"));
            if (status.Disks is not null)
            {
                foreach (var d in status.Disks)
                {
                    body.Children.Add(ViewHelpers.BodyText(
                        $"Disk {d.Drive}: {d.FreeGb:F1} GB free of {d.TotalGb:F1} GB"));
                }
            }
            if (status.Network is not null)
            {
                var n = status.Network;
                var down = n.DownMbps is null ? "—" : $"{n.DownMbps.Value:F1} Mbps";
                var up = n.UpMbps is null ? "—" : $"{n.UpMbps.Value:F1} Mbps";
                body.Children.Add(ViewHelpers.BodyText(
                    $"Network: {n.Interface ?? "—"} — down {down}, up {up}"));
            }
            if (status.UptimeHours is not null)
                body.Children.Add(ViewHelpers.BodyText($"Uptime: {status.UptimeHours.Value:F1} hours"));
            body.Children.Add(ViewHelpers.BodyText(
                status.LastSeenSecondsAgo is null
                    ? "Last seen: unknown"
                    : $"Last seen: {FormatAge(status.LastSeenSecondsAgo.Value)}"));
            ContentPanel.Children.Add(card);

            // M1: one-tap device commands. They go through chat, so
            // the model, the Worker and the Companion enforce their
            // allowlists exactly as if the user had typed them.
            var runCard = ViewHelpers.Card("Run on your laptop", out var runBody);
            runBody.Children.Add(ViewHelpers.MutedText(
                "These send a message in chat — ATHENA runs them on the laptop behind its allowlist. File writes still ask you on the laptop screen first."));
            foreach (var action in DeviceQuickActions.All)
            {
                var prompt = action.Prompt;
                var button = new Button
                {
                    Content = action.Label,
                    Background = Brushes.Transparent,
                    Foreground = (Brush)FindResource("BrushText"),
                    BorderThickness = new Thickness(0),
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    HorizontalContentAlignment = HorizontalAlignment.Left,
                    Padding = new Thickness(10, 6, 10, 6),
                    Margin = new Thickness(0, 2, 0, 2),
                    Cursor = System.Windows.Input.Cursors.Hand,
                    IsEnabled = status.Online && _sendDeviceCommand is not null,
                };
                button.Click += async (_, _) =>
                {
                    button.IsEnabled = false;
                    try
                    {
                        if (_sendDeviceCommand is not null)
                            await _sendDeviceCommand(prompt);
                    }
                    finally
                    {
                        button.IsEnabled = status.Online && _sendDeviceCommand is not null;
                    }
                };
                runBody.Children.Add(button);
            }
            if (!status.Online)
                runBody.Children.Add(ViewHelpers.MutedText(
                    "The laptop is offline, so commands are disabled until it is back."));
            ContentPanel.Children.Add(runCard);

            // M1: unpair. The revoke needs the device secret, which
            // lives only in the Companion app on the laptop — the
            // site key cannot revoke (the Worker answers 401, verified
            // live 2026-11-17). So this confirms the intent here and
            // shows the exact steps on the laptop. A site-key revoke
            // would need a small Worker change (owner decision — see
            // the N5 plan).
            var unpairCard = ViewHelpers.Card("Unpair this laptop", out var unpairBody);
            unpairBody.Children.Add(ViewHelpers.MutedText(
                "Revoking unpairs the laptop server-side: the Companion stops, queued commands are cleared, and the laptop must be paired again with a fresh code."));
            var unpairButton = new Button
            {
                Content = "Unpair this laptop…",
                HorizontalAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(10, 4, 10, 4),
                Margin = new Thickness(0, 6, 0, 0),
            };
            unpairButton.Click += (_, _) => ShowUnpairSteps(unpairBody, status.Device ?? "the laptop");
            unpairBody.Children.Add(unpairButton);
            ContentPanel.Children.Add(unpairCard);

            ContentPanel.Children.Add(ViewHelpers.MutedText(
                "Approvals appear as a popup on the laptop screen — ATHENA never approves anything by itself."));
            ContentPanel.Children.Add(ViewHelpers.MutedText(
                "Pairing and permissions are managed in the Windows Companion tray app on the laptop."));
        }
        catch (Exception ex)
        {
            ContentPanel.Children.Clear();
            ContentPanel.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
    }

    /// <summary>
    /// Unpair confirmation. The dialog states plainly that the
    /// revoke itself runs from the Companion tray app on the laptop
    /// (only it holds the device secret); confirming here shows the
    /// exact steps in the view. Nothing is revoked from this app.
    /// </summary>
    private static void ShowUnpairSteps(StackPanel body, string deviceName)
    {
        var answer = MessageBox.Show(
            $"Unpair '{deviceName}'?\n\nThis revokes the laptop server-side: the Companion stops, queued commands are cleared, and the laptop must be paired again with a fresh code.\n\nThe revoke itself runs from the Companion app on the laptop, because only it holds the device secret — the site key cannot revoke it. After confirming here, finish it on the laptop.",
            "Unpair laptop",
            MessageBoxButton.YesNo,
            MessageBoxImage.Warning);
        if (answer != MessageBoxResult.Yes) return;
        body.Children.Add(ViewHelpers.BodyText(
            "To finish unpairing: on the laptop, right-click the Companion tray icon → “Unpair this laptop” → confirm. The laptop is then revoked and shows the pairing screen again."));
    }

    private static string FormatAge(long seconds)
    {
        if (seconds < 60) return $"{seconds} second{(seconds == 1 ? "" : "s")} ago";
        if (seconds < 3600)
        {
            var minutes = seconds / 60;
            return $"{minutes} minute{(minutes == 1 ? "" : "s")} ago";
        }
        var hours = seconds / 3600;
        return $"{hours} hour{(hours == 1 ? "" : "s")} ago";
    }
}
