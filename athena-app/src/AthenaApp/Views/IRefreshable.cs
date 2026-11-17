namespace Athena.Views;

/// <summary>Every view can reload its data on demand.</summary>
public interface IRefreshable
{
    Task RefreshAsync(AppState state);
}
