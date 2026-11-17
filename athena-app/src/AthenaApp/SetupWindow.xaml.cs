using System.Net.Http;
using System.Windows;
using Athena.Core;

namespace Athena;

/// <summary>
/// First-run (or re-entry) setup: the user pastes the site key, it is
/// validated against the backend (no model call, no rows — see
/// ChatClient.ValidateKeyAsync), and only then stored encrypted.
/// </summary>
public partial class SetupWindow : Window
{
    private readonly ChatClient _client;

    public SetupWindow(ChatClient client)
    {
        _client = client;
        InitializeComponent();
        KeyBox.Focus();
    }

    private async void SaveButton_Click(object sender, RoutedEventArgs e)
    {
        var key = KeyBox.Password?.Trim() ?? "";
        if (key.Length < 16)
        {
            StatusText.Text = "That doesn't look like a site key — it should be a long string of letters and numbers.";
            return;
        }

        SaveButton.IsEnabled = false;
        StatusText.Text = "Checking the key…";
        try
        {
            var result = await _client.ValidateKeyAsync(key);
            switch (result)
            {
                case KeyValidationResult.Valid:
                    SecureStore.SaveKey(key);
                    StatusText.Text = "Key saved (encrypted).";
                    DialogResult = true;
                    Close();
                    break;
                case KeyValidationResult.Invalid:
                    StatusText.Text = "That key was rejected. Check it and try again.";
                    break;
                case KeyValidationResult.RateLimited:
                    StatusText.Text = "Too many attempts — wait a minute and try again.";
                    break;
                default:
                    StatusText.Text = "Couldn't reach the backend. Check your connection and try again.";
                    break;
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            StatusText.Text = "Couldn't reach the backend. Check your connection and try again.";
        }
        finally
        {
            SaveButton.IsEnabled = true;
        }
    }

    private void CancelButton_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }
}
