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

    public DevicesView(AppState state)
    {
        _state = state;
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
                    : $"Last seen {status.LastSeenSecondsAgo.Value} seconds ago"));
            ContentPanel.Children.Add(card);

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
}
