using System.Net.Http;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Athena.Core;

namespace Athena.Views;

/// <summary>Shared card / text factories so the views stay consistent.</summary>
internal static class ViewHelpers
{
    public const string NotEnabledDevice =
        "Device status isn't enabled yet — it needs the N2 read endpoints, waiting for approval.";
    public const string NotEnabledMemories =
        "Memory Vault isn't enabled yet — it needs the N2 read endpoints, waiting for approval.";
    public const string NotEnabledActivity =
        "Activity isn't enabled yet — it needs the N2 read endpoints, waiting for approval.";
    public const string OfflineMessage =
        "Couldn't reach ATHENA — check your connection and try again.";
    /// <summary>
    /// What to do after a failed refresh, for views with a Refresh
    /// button (Devices, Activity). Shown under the error itself so
    /// a failure always says what happened AND what to do next.
    /// </summary>
    public const string RetryHintRefresh = "Press Refresh to try again.";
    /// <summary>
    /// What to do after a failed refresh, for views with no Refresh
    /// button (Memory Vault refreshes when the section is opened).
    /// </summary>
    public const string RetryHintReopen =
        "Open this section again from the menu (···) to retry.";
    /// <summary>The placeholder shown in the Memory Vault detail pane when nothing is selected.</summary>
    public const string NoMemorySelected = "Select a memory to see the details.";

    public static Brush Gold => (Brush)Application.Current.FindResource("BrushGold");
    public static Brush Panel => (Brush)Application.Current.FindResource("BrushPanel");
    public static Brush CardBrush => (Brush)Application.Current.FindResource("BrushCard");
    public static Brush Text => (Brush)Application.Current.FindResource("BrushText");
    public static Brush Muted => (Brush)Application.Current.FindResource("BrushMuted");
    public static Brush Olive => (Brush)Application.Current.FindResource("BrushOlive");

    /// <summary>A bordered card with a gold title. Returns the card; the body stack is filled by the caller.</summary>
    public static Border Card(string title, out StackPanel body)
    {
        body = new StackPanel();
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock
        {
            Text = title,
            FontSize = 13,
            FontWeight = FontWeights.SemiBold,
            Foreground = Gold,
            Margin = new Thickness(0, 0, 0, 8),
        });
        stack.Children.Add(body);
        return new Border
        {
            Background = CardBrush,
            BorderBrush = Gold,
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6),
            Padding = new Thickness(14, 10, 14, 10),
            Margin = new Thickness(0, 0, 0, 12),
            Child = stack,
        };
    }

    public static TextBlock TitleText(string text) => new()
    {
        Text = text,
        FontSize = 14,
        FontWeight = FontWeights.SemiBold,
        Foreground = Text,
        TextWrapping = TextWrapping.Wrap,
    };

    public static TextBlock BodyText(string text) => new()
    {
        Text = text,
        FontSize = 13,
        Foreground = Text,
        TextWrapping = TextWrapping.Wrap,
        Margin = new Thickness(0, 2, 0, 2),
    };

    public static TextBlock MutedText(string text) => new()
    {
        Text = text,
        FontSize = 12,
        Foreground = Muted,
        TextWrapping = TextWrapping.Wrap,
        Margin = new Thickness(0, 2, 0, 2),
    };

    public static TextBlock StatusLine(string text, Color color) => new()
    {
        Text = text,
        FontSize = 13,
        FontWeight = FontWeights.SemiBold,
        Foreground = new SolidColorBrush(color),
        TextWrapping = TextWrapping.Wrap,
        Margin = new Thickness(0, 2, 0, 2),
    };

    /// <summary>Honest error text for a failed read: the server's own message, or the offline line.</summary>
    public static string FriendlyError(Exception ex) => ex switch
    {
        AthenaApiException api => api.Message,
        HttpRequestException or TaskCanceledException => OfflineMessage,
        _ => ex.Message,
    };

    /// <summary>A full-width row button: transparent, left-aligned, hand cursor.</summary>
    public static Button RowButton(UIElement content) => new()
    {
        Content = content,
        Background = Brushes.Transparent,
        Foreground = Text,
        BorderThickness = new Thickness(0),
        HorizontalAlignment = HorizontalAlignment.Stretch,
        HorizontalContentAlignment = HorizontalAlignment.Left,
        Padding = new Thickness(10, 8, 10, 8),
        Margin = new Thickness(0, 0, 0, 4),
        Cursor = System.Windows.Input.Cursors.Hand,
    };
}
