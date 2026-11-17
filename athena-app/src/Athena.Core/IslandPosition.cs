namespace Athena.Core;

/// <summary>
/// Where the Dynamic Island (N3) sits on screen, persisted
/// between runs. Plain JSON next to the DPAPI config — position
/// is not a secret. Pure maths here so the harness can test it
/// on Linux without a window.
/// </summary>
public sealed record IslandPosition(double Left, double Top, bool AlwaysOnTop, bool Visible);

public static class IslandPositionMath
{
    /// <summary>
    /// Clamp a window of (width, height) so it sits fully inside a
    /// screen of (screenWidth, screenHeight). If the screen is
    /// smaller than the window, pin to the top-left corner.
    /// </summary>
    public static (double Left, double Top) Clamp(
        double left, double top, double width, double height,
        double screenWidth, double screenHeight)
    {
        var maxLeft = Math.Max(0, screenWidth - width);
        var maxTop = Math.Max(0, screenHeight - height);
        return (
            Math.Clamp(left, 0, maxLeft),
            Math.Clamp(top, 0, maxTop));
    }

    /// <summary>
    /// Default spot: bottom-right, 24 px from the edges. Used on
    /// first run, when the saved file is missing, or when the saved
    /// position is off every connected screen.
    /// </summary>
    public static IslandPosition Default(
        double screenWidth, double screenHeight, double width, double height)
    {
        var (left, top) = Clamp(
            screenWidth - width - 24, screenHeight - height - 24,
            width, height, screenWidth, screenHeight);
        return new IslandPosition(left, top, AlwaysOnTop: true, Visible: true);
    }
}
