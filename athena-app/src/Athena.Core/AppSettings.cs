namespace Athena.Core;

/// <summary>
/// App-level preferences (N8), persisted as plain JSON in
/// %APPDATA%\Athena\settings.json — no secrets here. This is the
/// home for settings that are not voice preferences: autostart
/// lives here, NOT in voice.json, because it has nothing to do
/// with voice (voice.json is voice output/input/wake-word only).
/// AutostartEnabled is ON by default in this build because the
/// user explicitly asked for autostart; it is an init-only
/// property with a true default (not a positional constructor
/// parameter) so an old settings.json without the field
/// deserializes to on, matching the VoiceSettings
/// WakeWordEnabled pattern (N7).
/// </summary>
public sealed record AppSettings
{
    public bool AutostartEnabled { get; init; } = true;

    public static AppSettings Defaults => new();
}
