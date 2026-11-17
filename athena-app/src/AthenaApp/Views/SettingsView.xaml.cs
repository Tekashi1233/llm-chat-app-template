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
            !string.IsNullOrWhiteSpace(state.AuthToken)
                ? "Signed in: Yes — session token stored (encrypted)"
                : !string.IsNullOrWhiteSpace(state.ApiKey)
                ? "Signed in: using the site key (grace period — it will be swapped for a session token)"
                : "Signed in: No"));
        var changeKeyButton = new Button
        {
            Content = string.IsNullOrWhiteSpace(state.AuthToken) && string.IsNullOrWhiteSpace(state.ApiKey)
                ? "Sign in"
                : "Sign in again",
            HorizontalAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(10, 4, 10, 4),
            Margin = new Thickness(0, 6, 0, 0),
        };
        changeKeyButton.Click += ChangeKeyButton_Click;
        connBody.Children.Add(changeKeyButton);
        if (!string.IsNullOrWhiteSpace(state.AuthToken))
        {
            var signOutButton = new Button
            {
                Content = "Sign out",
                HorizontalAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(10, 4, 10, 4),
                Margin = new Thickness(0, 6, 0, 0),
            };
            signOutButton.Click += SignOutButton_Click;
            connBody.Children.Add(signOutButton);
        }
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

        // Voice (N4): real controls. Output is OFF by default;
        // everything persists to voice.json on change.
        var voiceCard = ViewHelpers.Card("Voice", out var voiceBody);
        var voiceSvc = state.Voice;
        if (voiceSvc is null || !voiceSvc.IsAvailable)
        {
            voiceBody.Children.Add(ViewHelpers.BodyText(
                "Voice output isn't available on this PC — no speech synthesis voices were found. " +
                "Chat still works; only the spoken replies and microphone are missing."));
        }
        else
        {
            // Voice picker: installed voices, female en-GB first.
            var installed = voiceSvc.InstalledVoices;
            voiceBody.Children.Add(ViewHelpers.BodyText("Voice"));
            var voiceCombo = new ComboBox
            {
                HorizontalAlignment = HorizontalAlignment.Left,
                MinWidth = 260,
                Margin = new Thickness(0, 2, 0, 8),
            };
            var displayNames = installed
                .Select(v => $"{v.Name} ({v.CultureName})")
                .ToList();
            voiceCombo.ItemsSource = displayNames;
            var current = VoiceSelection.Pick(installed, state.VoiceSettings.VoiceName);
            var currentIndex = current is null ? -1 : installed.ToList().IndexOf(current);
            voiceCombo.SelectedIndex = currentIndex;
            voiceCombo.SelectionChanged += (_, _) =>
            {
                var idx = voiceCombo.SelectedIndex;
                if (idx < 0 || idx >= installed.Count) return;
                _state.VoiceSettings = _state.VoiceSettings with
                {
                    VoiceName = installed[idx].Name,
                };
                _state.SaveVoiceSettings();
                voiceSvc.ApplySettings(_state.VoiceSettings);
            };
            voiceBody.Children.Add(voiceCombo);

            // Speed slider (-10..10).
            var speedLabel = ViewHelpers.BodyText(
                $"Speed: {state.VoiceSettings.Rate} (0 is normal)");
            voiceBody.Children.Add(speedLabel);
            var speedSlider = new Slider
            {
                Minimum = -10, Maximum = 10,
                Value = state.VoiceSettings.Rate,
                Width = 260, HorizontalAlignment = HorizontalAlignment.Left,
                Margin = new Thickness(0, 2, 0, 8),
                TickFrequency = 1, IsSnapToTickEnabled = true,
            };
            speedSlider.ValueChanged += (_, e) =>
            {
                var rate = (int)Math.Round(e.NewValue);
                speedLabel.Text = $"Speed: {rate} (0 is normal)";
                _state.VoiceSettings = _state.VoiceSettings with { Rate = rate };
                _state.SaveVoiceSettings();
                voiceSvc.ApplySettings(_state.VoiceSettings);
            };
            voiceBody.Children.Add(speedSlider);

            // Volume slider (0..100).
            var volumeLabel = ViewHelpers.BodyText(
                $"Volume: {state.VoiceSettings.Volume}");
            voiceBody.Children.Add(volumeLabel);
            var volumeSlider = new Slider
            {
                Minimum = 0, Maximum = 100,
                Value = state.VoiceSettings.Volume,
                Width = 260, HorizontalAlignment = HorizontalAlignment.Left,
                Margin = new Thickness(0, 2, 0, 8),
                TickFrequency = 5, IsSnapToTickEnabled = true,
            };
            volumeSlider.ValueChanged += (_, e) =>
            {
                var volume = (int)Math.Round(e.NewValue);
                volumeLabel.Text = $"Volume: {volume}";
                _state.VoiceSettings = _state.VoiceSettings with { Volume = volume };
                _state.SaveVoiceSettings();
                voiceSvc.ApplySettings(_state.VoiceSettings);
            };
            voiceBody.Children.Add(volumeSlider);

            // Output toggle.
            var outputCheck = new CheckBox
            {
                Content = "Read replies aloud",
                IsChecked = state.VoiceSettings.OutputEnabled,
                Margin = new Thickness(0, 2, 0, 4),
            };
            outputCheck.Checked += (_, _) => SetOutputEnabled(true);
            outputCheck.Unchecked += (_, _) => SetOutputEnabled(false);
            voiceBody.Children.Add(outputCheck);

            // Test button: speaks a sample with the current voice,
            // even when read-aloud is off (it is an explicit test).
            var testButton = new Button
            {
                Content = "Test voice",
                HorizontalAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(10, 4, 10, 4),
                Margin = new Thickness(0, 4, 0, 8),
            };
            testButton.Click += (_, _) =>
                voiceSvc.Speak("Hello, I am ATHENA, your personal assistant.");
            voiceBody.Children.Add(testButton);

            void SetOutputEnabled(bool on)
            {
                _state.VoiceSettings = _state.VoiceSettings with { OutputEnabled = on };
                _state.SaveVoiceSettings();
                if (!on) voiceSvc.StopSpeaking();
            }
        }

        // Microphone input.
        voiceBody.Children.Add(ViewHelpers.BodyText("Microphone"));
        if (voiceSvc is null || !voiceSvc.IsRecognizerAvailable)
        {
            voiceBody.Children.Add(ViewHelpers.MutedText(
                "Microphone input isn't available on this PC — no speech recognizer or microphone was found."));
        }
        else
        {
            voiceBody.Children.Add(ViewHelpers.BodyText(
                $"Speech recognition: {voiceSvc.RecognizerCultureName}"));
            var micCheck = new CheckBox
            {
                Content = "Microphone input enabled",
                IsChecked = state.VoiceSettings.MicEnabled,
                Margin = new Thickness(0, 2, 0, 0),
            };
            micCheck.Checked += (_, _) => SetMicEnabled(true);
            micCheck.Unchecked += (_, _) => SetMicEnabled(false);
            voiceBody.Children.Add(micCheck);
            voiceBody.Children.Add(ViewHelpers.MutedText(
                "Tap Mic in Chat to speak. Your words go into the message box for you to check — nothing is sent automatically, and the microphone is never on unless you tap it."));

            void SetMicEnabled(bool on)
            {
                _state.VoiceSettings = _state.VoiceSettings with { MicEnabled = on };
                _state.SaveVoiceSettings();
            }
        }
        ContentPanel.Children.Add(voiceCard);

        // Device.
        var deviceCard = ViewHelpers.Card("Device", out var deviceBody);
        try
        {
            if (!state.HasAuth)
            {
                deviceBody.Children.Add(ViewHelpers.MutedText("No laptop paired"));
            }
            else
            {
                var status = await state.Data.GetDeviceStatusAsync(state.ApiKey, authToken: state.AuthToken);
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
            "ATHENA — minimal black identity + voice (N4). Native Windows app, C# + WPF + .NET 8. Backend: Cloudflare Worker + GPT-OSS-20B + D1."));
        ContentPanel.Children.Add(aboutCard);
    }

    private async void ChangeKeyButton_Click(object sender, RoutedEventArgs e)
    {
        var setup = new SetupWindow(_state.Chat, _state.Auth);
        if (setup.ShowDialog() == true)
        {
            _state.AuthToken = SecureStore.TryLoadToken();
            _state.ApiKey = SecureStore.TryLoadKey();
            await _onKeyChanged();
            await RefreshAsync(_state);
        }
    }

    /// <summary>
    /// N5 M2 sign out: revoke the session server-side (best effort —
    /// a failed call never blocks signing out locally), then delete
    /// the stored token. The conversation history stays on the server.
    /// </summary>
    private async void SignOutButton_Click(object sender, RoutedEventArgs e)
    {
        var token = _state.AuthToken;
        if (!string.IsNullOrWhiteSpace(token))
        {
            try { await _state.Auth.LogoutAsync(token); } catch (Exception) { }
        }
        SecureStore.DeleteToken();
        _state.AuthToken = null;
        await _onKeyChanged();
        await RefreshAsync(_state);
    }
}
