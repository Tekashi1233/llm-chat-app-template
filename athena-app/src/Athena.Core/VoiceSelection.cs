namespace Athena.Core;

/// <summary>
/// One installed speech voice, as a plain DTO so the selection
/// policy (VoiceSelection) stays free of System.Speech types and
/// testable on Linux. The app maps each
/// System.Speech.Synthesis.InstalledVoice to this record.
/// </summary>
public enum VoiceGender
{
    Female,
    Male,
    Neutral,
    Unknown,
}

public sealed record VoiceInfo(string Name, string CultureName, VoiceGender Gender);

/// <summary>
/// Pure voice-selection policy (N4). Never hardcodes a single
/// voice as the only option: the user can pick any installed
/// voice in Settings, and this is only the automatic default.
/// Order: an explicitly saved voice name first; then Microsoft
/// Hazel Desktop (the classic offline en-GB female voice); then
/// any female en-GB voice; then any en-GB voice; then any
/// female voice; then the first installed voice (the system
/// default). No installed voices → null.
/// </summary>
public static class VoiceSelection
{
    public static VoiceInfo? Pick(IReadOnlyList<VoiceInfo> voices, string? preferredName)
    {
        if (voices.Count == 0) return null;

        if (!string.IsNullOrWhiteSpace(preferredName))
        {
            var named = voices.FirstOrDefault(v =>
                v.Name.Equals(preferredName, StringComparison.OrdinalIgnoreCase));
            if (named is not null) return named;
        }

        var hazel = voices.FirstOrDefault(v =>
            IsEnGb(v.CultureName) &&
            v.Name.Contains("Hazel", StringComparison.OrdinalIgnoreCase));
        if (hazel is not null) return hazel;

        var femaleGb = voices.FirstOrDefault(v =>
            v.Gender == VoiceGender.Female && IsEnGb(v.CultureName));
        if (femaleGb is not null) return femaleGb;

        var anyGb = voices.FirstOrDefault(IsEnGbVoice);
        if (anyGb is not null) return anyGb;

        var female = voices.FirstOrDefault(v => v.Gender == VoiceGender.Female);
        if (female is not null) return female;

        return voices[0];
    }

    /// <summary>Display order for the Settings voice list: female en-GB first, then en-GB, then female, then the rest.</summary>
    public static IReadOnlyList<VoiceInfo> OrderForDisplay(IReadOnlyList<VoiceInfo> voices) =>
        voices
            .OrderBy(v => v.Gender == VoiceGender.Female && IsEnGb(v.CultureName) ? 0
                : IsEnGb(v.CultureName) ? 1
                : v.Gender == VoiceGender.Female ? 2 : 3)
            .ThenBy(v => v.Name, StringComparer.OrdinalIgnoreCase)
            .ToList();

    public static bool IsEnGb(string cultureName) =>
        cultureName.Equals("en-GB", StringComparison.OrdinalIgnoreCase) ||
        cultureName.StartsWith("en-GB-", StringComparison.OrdinalIgnoreCase);

    private static bool IsEnGbVoice(VoiceInfo v) => IsEnGb(v.CultureName);
}
