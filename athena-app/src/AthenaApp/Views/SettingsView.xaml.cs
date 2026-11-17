using System.Windows;
using System.Windows.Controls;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// Settings: connection, model, voice (N4 placeholder), device,
/// permissions and about. Read-only information plus the key
/// change flow; no new privileges are introduced here.
/// </summary>
public partial class SettingsView : UserControl, IRefreshable
{
    private readonly AppState _state;
    private readonly Func<Task> _onKeyChanged;

    public SettingsView(AppState state, Func<Task> onKeyChanged)
    {
        _state = state;
        _onKeyChanged = onKeyChanged;
        InitializeComponent();
    }

    public async Task RefreshAsync(AppState state)
    {
        ContentPanel.Children.Clear();

        // Connection.
        var connCard = ViewHelpers.Card("Connection", out var connBody);
        connBody.Children.Add(ViewHelpers.BodyText("Backend: " + AthenaApi.WorkerBase));
        connBody.Children.Add(ViewHelpers.BodyText(
            string.IsNullOrWhiteSpace(state.ApiKey)
                ? "Key stored: No key stored"
                : "Key stored: Key stored (encrypted)"));
        var changeKeyButton = new Button
        {
            Content = "Change key",
            HorizontalAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(10, 4, 10, 4),
            Margin = new Thickness(0, 6, 0, 0),
        };
        changeKeyButton.Click += ChangeKeyButton_Click;
        connBody.Children.Add(changeKeyButton);
        ContentPanel.Children.Add(connCard);

        // Model.
        var modelCard = ViewHelpers.Card("Model", out var modelBody);
        try
        {
            state.ModelInfo ??= await state.Data.GetModelInfoAsync();
            var m = state.ModelInfo;
            if (m is null)
            {
                modelBody.Children.Add(ViewHelpers.MutedText("couldn't read model info"));
            }
            else
            {
                modelBody.Children.Add(ViewHelpers.BodyText("Assistant: " + (m.Assistant ?? "—")));
                modelBody.Children.Add(ViewHelpers.BodyText("Model: " + (m.ConfiguredModel ?? "—")));
                modelBody.Children.Add(ViewHelpers.BodyText("Reasoning: " + (m.ReasoningEffort ?? "—")));
                modelBody.Children.Add(ViewHelpers.BodyText(
                    "Streaming: " + (m.Streaming ? "yes" : "no")));
                modelBody.Children.Add(ViewHelpers.BodyText("Tools: " + m.Tools.Length + " registered"));
            }
        }
        catch (Exception ex)
        {
            modelBody.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
        ContentPanel.Children.Add(modelCard);

        // Voice (N4 placeholder).
        var voiceCard = ViewHelpers.Card("Voice", out var voiceBody);
        voiceBody.Children.Add(ViewHelpers.BodyText(
            "Voice arrives in N4 — female British voice, speech input and output. Nothing to set up yet."));
        ContentPanel.Children.Add(voiceCard);

        // Island (N3).
        var islandCard = ViewHelpers.Card("Island", out var islandBody);
        islandBody.Children.Add(ViewHelpers.BodyText(
            "The floating Island is a small pill that shows what ATHENA is doing — idle, thinking, offline or error. " +
            "Drag it anywhere; single-click expands it to the last reply, double-click opens the Command Centre. " +
            "Right-click it for always-on-top and hide."));
        var showIslandButton = new Button
        {
            Content = "Show the floating Island",
            HorizontalAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(10, 4, 10, 4),
            Margin = new Thickness(0, 6, 0, 0),
        };
        showIslandButton.Click += (_, _) => _state.RequestShowIsland();
        islandBody.Children.Add(showIslandButton);
        ContentPanel.Children.Add(islandCard);

        // Device.
        var deviceCard = ViewHelpers.Card("Device", out var deviceBody);
        try
        {
            if (string.IsNullOrWhiteSpace(state.ApiKey))
            {
                deviceBody.Children.Add(ViewHelpers.MutedText("No laptop paired"));
            }
            else
            {
                var status = await state.Data.GetDeviceStatusAsync(state.ApiKey);
                if (status is null)
                {
                    // Endpoint not deployed yet: say so honestly instead
                    // of claiming no laptop is paired.
                    deviceBody.Children.Add(ViewHelpers.MutedText(ViewHelpers.NotEnabledDevice));
                }
                else if (status.Device is null)
                {
                    deviceBody.Children.Add(ViewHelpers.MutedText("No laptop paired"));
                }
                else
                {
                    deviceBody.Children.Add(ViewHelpers.BodyText(
                        $"{status.Device} — {(status.Online ? "online" : "offline")}"));
                    if (status.LastSeenSecondsAgo is not null)
                    {
                        deviceBody.Children.Add(ViewHelpers.MutedText(
                            $"Last seen {status.LastSeenSecondsAgo.Value} seconds ago"));
                    }
                }
            }
        }
        catch (Exception ex)
        {
            deviceBody.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
        ContentPanel.Children.Add(deviceCard);

        // Permissions.
        var permCard = ViewHelpers.Card("Permissions", out var permBody);
        permBody.Children.Add(ViewHelpers.BodyText(
            "App, folder and command approvals live in the Windows Companion tray app on your laptop."));
        ContentPanel.Children.Add(permCard);

        // About.
        var aboutCard = ViewHelpers.Card("About", out var aboutBody);
        aboutBody.Children.Add(ViewHelpers.BodyText(
            "ATHENA — N3 Command Centre + Dynamic Island. Native Windows app, C# + WPF + .NET 8. Backend: Cloudflare Worker + GPT-OSS-20B + D1."));
        ContentPanel.Children.Add(aboutCard);
    }

    private async void ChangeKeyButton_Click(object sender, RoutedEventArgs e)
    {
        var setup = new SetupWindow(_state.Chat);
        if (setup.ShowDialog() == true)
        {
            _state.ApiKey = SecureStore.TryLoadKey();
            await _onKeyChanged();
            await RefreshAsync(_state);
        }
    }
}
