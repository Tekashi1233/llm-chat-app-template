namespace Athena.Core;

/// <summary>
/// Voice preferences (N4), persisted as plain JSON in
/// %APPDATA%\Athena\voice.json — no secrets here. Output is OFF
/// by default, matching the website. Rate is the SAPI range
/// -10..10 (0 = normal); Volume is 0..100. RecognitionCulture
/// is the speech-recognition language ("en-GB" first, "en-US"
/// as the fallback when no en-GB recognizer is installed).
/// WakeWordEnabled (N7) is ON by default: saying "athena" pops
/// the interface up. It is a non-positional init-only property
/// (not a constructor parameter) so existing positional
/// constructions — and old voice.json files without the field,
/// which deserialize to the true default — keep working.
/// GreetOnWakeWord (N8) follows the same pattern: ON by default,
/// the greeting "Welcome, boss." is spoken when the wake word
/// pops the window up, even when reply voice output is OFF.
/// </summary>
public sealed record VoiceSettings(
    bool OutputEnabled,
    string? VoiceName,
    int Rate,
    int Volume,
    bool MicEnabled,
    string RecognitionCulture)
{
    /// <summary>Wake-word listening (N7): on by default; an explicit false is persisted and respected.</summary>
    public bool WakeWordEnabled { get; init; } = true;

    /// <summary>Wake greeting (N8): on by default; an explicit false is persisted and respected. Off silences the greeting — the window still pops up.</summary>
    public bool GreetOnWakeWord { get; init; } = true;

    public static VoiceSettings Defaults =>
        new(OutputEnabled: false, VoiceName: null, Rate: 0,
            Volume: 100, MicEnabled: true, RecognitionCulture: "en-GB");

    /// <summary>Clamp Rate/Volume into their SAPI ranges and repair a blank recognition culture.</summary>
    public VoiceSettings Clamped() => this with
    {
        Rate = Math.Clamp(Rate, -10, 10),
        Volume = Math.Clamp(Volume, 0, 100),
        RecognitionCulture = string.IsNullOrWhiteSpace(RecognitionCulture)
            ? "en-GB"
            : RecognitionCulture.Trim(),
    };
}
