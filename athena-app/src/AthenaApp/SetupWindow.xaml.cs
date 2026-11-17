using System.Net.Http;
using System.Windows;
using Athena.Core;

namespace Athena;

/// <summary>
/// N5 M2 sign-in: the password login screen. Three modes, decided on load from
/// GET /api/auth/status:
///   Login — an owner password already exists; password + sign in
///     (POST /api/auth/login). The returned token is stored
///     DPAPI-encrypted; the password itself is never stored.
///   Setup — no owner exists yet (setup_required); password +
///     confirm + a one-time site-key field (POST /api/auth/setup
///     with the key as its auth header). The key is used for that
///     one call only and is never stored. DORMANT: the owner does not
///     want a password (2026-11-18), so no-owner now opens LegacyKey.
///   LegacyKey — the site-key flow. Used when the auth endpoints are
///     not deployed yet (the status call answers 404) AND when no
///     owner exists (setup_required): no password, just the site key,
///     offered as the grace-period fallback.
/// The caller reads the result back with SecureStore.TryLoadToken()
/// (Login/Setup) or SecureStore.TryLoadKey() (LegacyKey).
/// </summary>
public partial class SetupWindow : Window
{
    private readonly ChatClient _chat;
    private readonly AuthClient _auth;
    private SetupMode _mode = SetupMode.Login;

    public SetupWindow(ChatClient chat, AuthClient auth)
    {
        _chat = chat;
        _auth = auth;
        InitializeComponent();
        Loaded += async (_, _) => await DetermineModeAsync();
        PasswordBox.Focus();
    }

    internal enum SetupMode { Login, Setup, LegacyKey }

    private async Task DetermineModeAsync()
    {
        try
        {
            var status = await _auth.GetStatusAsync();
            if (status is null)
            {
                // Auth endpoints not deployed yet: old key flow.
                SetMode(SetupMode.LegacyKey);
                return;
            }
            // No owner yet: no password is wanted, so use the site key.
            SetMode(status.SetupRequired ? SetupMode.LegacyKey : SetupMode.Login);
        }
        catch (Exception)
        {
            // N6: offline OR a bad/unexpected answer — either way
            // the sign-in state could not be checked. Fall back to
            // the site-key flow (the no-password default): the key
            // check reports the connection problem honestly when
            // it is tried, instead of showing a password screen
            // for a password that does not exist.
            SetMode(SetupMode.LegacyKey);
            StatusText.Text = "Couldn't reach the backend — check your connection, then try signing in.";
        }
    }

    private void SetMode(SetupMode mode)
    {
        _mode = mode;
        switch (mode)
        {
            case SetupMode.Setup:
                Title = "Set up ATHENA — choose a password";
                HeadingText.Text = "Set your password";
                IntroText.Text = "This is the first run: choose the password you will sign in " +
                    "with on every device. At least 8 characters — you set it here, " +
                    "never in chat. The one-time site key below proves this setup " +
                    "call is yours; it is used once and never stored.";
                ConfirmLabel.Visibility = Visibility.Visible;
                ConfirmBox.Visibility = Visibility.Visible;
                KeyLabel.Visibility = Visibility.Visible;
                KeyBox.Visibility = Visibility.Visible;
                SaveButton.Content = "Set password & sign in";
                break;
            case SetupMode.LegacyKey:
                Title = "Set up ATHENA";
                HeadingText.Text = "Your site key";
                IntroText.Text = "No password is set up, so sign in with " +
                    "the site key (the ATHENA_KEY value in vesper-cloud.html). " +
                    "It is checked once, then stored encrypted with Windows DPAPI on " +
                    "this computer only.";
                ConfirmLabel.Visibility = Visibility.Collapsed;
                ConfirmBox.Visibility = Visibility.Collapsed;
                KeyLabel.Visibility = Visibility.Visible;
                KeyBox.Visibility = Visibility.Visible;
                SaveButton.Content = "Validate & save";
                break;
            default:
                Title = "Sign in to ATHENA";
                HeadingText.Text = "Sign in";
                IntroText.Text = "Sign in with your ATHENA password. Your session is stored " +
                    "encrypted with Windows DPAPI on this computer only.";
                ConfirmLabel.Visibility = Visibility.Collapsed;
                ConfirmBox.Visibility = Visibility.Collapsed;
                KeyLabel.Visibility = Visibility.Collapsed;
                KeyBox.Visibility = Visibility.Collapsed;
                SaveButton.Content = "Sign in";
                break;
        }
    }

    private async void SaveButton_Click(object sender, RoutedEventArgs e)
    {
        SaveButton.IsEnabled = false;
        try
        {
            switch (_mode)
            {
                case SetupMode.Setup:
                    await SetupAsync();
                    break;
                case SetupMode.LegacyKey:
                    await LegacyKeyAsync();
                    break;
                default:
                    await LoginAsync();
                    break;
            }
        }
        finally
        {
            SaveButton.IsEnabled = true;
        }
    }

    private async Task LoginAsync()
    {
        var password = PasswordBox.Password ?? "";
        if (password.Length == 0)
        {
            StatusText.Text = "Enter your password.";
            return;
        }
        StatusText.Text = "Signing in…";
        try
        {
            var token = await _auth.LoginAsync(password);
            SecureStore.SaveToken(token.Token);
            StatusText.Text = "Signed in.";
            DialogResult = true;
            Close();
        }
        catch (AthenaApiException ex) when (ex.StatusCode == 401)
        {
            StatusText.Text = "Wrong password. Try again.";
        }
        catch (AthenaApiException ex) when (ex.StatusCode == 429)
        {
            StatusText.Text = "Too many attempts — wait a minute and try again.";
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            StatusText.Text = "Couldn't reach the backend. Check your connection and try again.";
        }
        catch (Exception)
        {
            // N6: a malformed answer or a local save failure ends
            // here as an honest message, never as a crash out of
            // the click handler.
            StatusText.Text = "Something went wrong — check your connection and try again.";
        }
    }

    private async Task SetupAsync()
    {
        var password = PasswordBox.Password ?? "";
        var confirm = ConfirmBox.Password ?? "";
        var key = KeyBox.Password?.Trim() ?? "";
        if (!AuthValidation.IsValidPassword(password))
        {
            StatusText.Text = "Choose a password of at least 8 characters.";
            return;
        }
        if (!AuthValidation.PasswordsMatch(password, confirm))
        {
            StatusText.Text = "The two passwords don't match.";
            return;
        }
        if (key.Length < 16)
        {
            StatusText.Text = "That doesn't look like a site key — it should be a long string of letters and numbers.";
            return;
        }
        StatusText.Text = "Setting your password…";
        try
        {
            var token = await _auth.SetupAsync(password, key);
            SecureStore.SaveToken(token.Token);
            StatusText.Text = "Password set — signed in.";
            DialogResult = true;
            Close();
        }
        catch (AthenaApiException ex) when (ex.StatusCode == 409)
        {
            StatusText.Text = "A password is already set — sign in instead. Close this and try again.";
        }
        catch (AthenaApiException ex) when (ex.StatusCode == 401)
        {
            StatusText.Text = "That site key was rejected. Check it and try again.";
        }
        catch (AthenaApiException ex) when (ex.StatusCode == 429)
        {
            StatusText.Text = "Too many attempts — wait a minute and try again.";
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            StatusText.Text = "Couldn't reach the backend. Check your connection and try again.";
        }
        catch (Exception)
        {
            // N6: a malformed answer or a local save failure ends
            // here as an honest message, never as a crash out of
            // the click handler.
            StatusText.Text = "Something went wrong — check your connection and try again.";
        }
    }

    private async Task LegacyKeyAsync()
    {
        var key = KeyBox.Password?.Trim() ?? "";
        if (key.Length < 16)
        {
            StatusText.Text = "That doesn't look like a site key — it should be a long string of letters and numbers.";
            return;
        }
        StatusText.Text = "Checking the key…";
        try
        {
            var result = await _chat.ValidateKeyAsync(key);
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
        catch (Exception)
        {
            // N6: a malformed answer or a local save failure ends
            // here as an honest message, never as a crash out of
            // the click handler.
            StatusText.Text = "Something went wrong — check your connection and try again.";
        }
    }

    private void CancelButton_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }
}
