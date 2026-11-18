namespace Athena.Core;

/// <summary>
/// Wake-word trigger policy (N7). Pure logic — no I/O, no SAPI — so
/// the harness can test it on Linux. The app feeds it each
/// recognition result and pops the interface up when it returns
/// true. Note the SAPI grammar only ever emits the single word
/// "athena" anyway, so the exact-word check here is
/// defense-in-depth against a dictation result (or a future
/// grammar change) slipping a longer phrase through.
/// </summary>
public sealed class WakeWordTrigger
{
    /// <summary>Minimum recognition confidence for a trigger to count (SAPI reports 0..1).</summary>
    public const double MinConfidence = 0.7;

    /// <summary>Repeats within this many seconds of the last accepted trigger are ignored.</summary>
    public const int CooldownSeconds = 3;

    private const string WakeWord = "athena";

    private DateTimeOffset? _lastAccepted;

    /// <summary>When the last trigger was accepted, if any — useful for diagnostics, not for the decision itself.</summary>
    public DateTimeOffset? LastAcceptedAt => _lastAccepted;

    /// <summary>
    /// Returns true only when all three rules pass: the text is
    /// exactly the single word "athena" after normalization
    /// (trimmed, case-insensitive, leading/trailing punctuation
    /// stripped — "Athena!" passes, "athena please" and "" fail),
    /// the confidence meets <see cref="MinConfidence"/>, and at
    /// least <see cref="CooldownSeconds"/> have passed since the
    /// last accepted trigger. An accepted trigger resets the
    /// cooldown; a rejected one does not.
    /// </summary>
    public bool TryTrigger(string? text, double confidence, DateTimeOffset now)
    {
        if (double.IsNaN(confidence) || confidence < MinConfidence)
            return false;
        if (!IsWakeWord(text))
            return false;
        if (_lastAccepted.HasValue
            && (now - _lastAccepted.Value).TotalSeconds < CooldownSeconds)
            return false;

        _lastAccepted = now;
        return true;
    }

    private static bool IsWakeWord(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return false;
        var t = text.Trim();
        var start = 0;
        var end = t.Length;
        while (start < end
            && (char.IsWhiteSpace(t[start]) || char.IsPunctuation(t[start])))
            start++;
        while (end > start
            && (char.IsWhiteSpace(t[end - 1]) || char.IsPunctuation(t[end - 1])))
            end--;
        return end - start == WakeWord.Length
            && string.Compare(t, start, WakeWord, 0, WakeWord.Length,
                StringComparison.OrdinalIgnoreCase) == 0;
    }
}
