using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Athena.Core;

namespace Athena.Views;

/// <summary>
/// The 13 registered tools, as documentation: name, description,
/// category and whether the server currently lists the tool.
/// Nothing here executes anything.
/// </summary>
public partial class ToolsView : UserControl, IRefreshable
{
    private readonly AppState _state;

    public ToolsView(AppState state)
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
            state.ModelInfo ??= await state.Data.GetModelInfoAsync();
        }
        catch (Exception ex)
        {
            // Model info is optional here: availability just becomes unknown.
            ContentPanel.Children.Clear();
            ContentPanel.Children.Add(ViewHelpers.BodyText(ViewHelpers.FriendlyError(ex)));
            RenderTools(state.ModelInfo);
            return;
        }
        ContentPanel.Children.Clear();
        RenderTools(state.ModelInfo);
    }

    private void RenderTools(ModelInfo? modelInfo)
    {
        foreach (var tool in ToolCatalog.All)
        {
            var card = ViewHelpers.Card(tool.Name, out var body);
            body.Children.Add(ViewHelpers.BodyText(tool.Description));
            body.Children.Add(ViewHelpers.MutedText("Category: " + tool.Category));
            if (modelInfo is null)
            {
                body.Children.Add(ViewHelpers.StatusLine(
                    "Availability unknown — couldn't read model info.", Colors.Gray));
            }
            else if (tool.IsRegistered(modelInfo))
            {
                body.Children.Add(ViewHelpers.StatusLine("Registered", Colors.Green));
            }
            else
            {
                body.Children.Add(ViewHelpers.StatusLine("Not listed by the server", Colors.Gray));
            }
            ContentPanel.Children.Add(card);
        }
    }
}
