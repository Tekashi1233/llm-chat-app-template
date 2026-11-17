using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// Read-only audit timeline from the device_actions table via the
/// N2 activity endpoint. Rows are displayed, never modified.
/// </summary>
public partial class ActivityView : UserControl, IRefreshable
{
    private readonly AppState _state;

    public ActivityView(AppState state)
    {
        _state = state;
        InitializeComponent();
    }

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
                    "Set your site key first to see activity."));
                return;
            }

            var list = await state.Data.GetDeviceActivityAsync(state.ApiKey, limit: 50);
            ContentPanel.Children.Clear();
            if (list is null)
            {
                ContentPanel.Children.Add(ViewHelpers.MutedText(ViewHelpers.NotEnabledActivity));
                return;
            }
            if (list.Actions.Length == 0)
            {
                ContentPanel.Children.Add(ViewHelpers.MutedText("No activity recorded yet."));
                return;
            }

            foreach (var a in list.Actions)
            {
                var card = ViewHelpers.Card(a.Kind, out var body);
                body.Children.Add(ViewHelpers.BodyText(a.Detail));
                body.Children.Add(ViewHelpers.MutedText(
                    ConversationStore.RelativeTime(a.CreatedAt)));
                body.Children.Add(ViewHelpers.StatusLine(
                    a.WasApproved ? "Approved" : "Refused",
                    a.WasApproved ? Colors.Green : Colors.Red));
                ContentPanel.Children.Add(card);
            }
        }
        catch (Exception ex)
        {
            ContentPanel.Children.Clear();
            ContentPanel.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
        }
    }
}
