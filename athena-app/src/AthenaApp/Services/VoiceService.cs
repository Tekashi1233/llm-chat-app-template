using System.Globalization;
using System.Speech.Recognition;
using System.Speech.Synthesis;
using Athena.Core;
using CoreVoiceInfo = Athena.Core.VoiceInfo;

namespace Athena;

/// <summary>
/// Native Windows voice (N4) over SAPI via System.Speech:
/// speech output (SpeechSynthesizer) and speech input
/// (SpeechRecognitionEngine). Constructed once on the UI thread
/// by MainWindow; every SAPI call is guarded, so a machine
/// without SAPI, without voices, or without a microphone
/// degrades honestly instead of crashing — check IsAvailable /
/// IsRecognizerAvailable before offering the controls.
///
/// All callbacks are marshalled back to the synchronization
/// context captured at construction (the UI thread), so event
/// handlers may touch WPF controls and AppState safely.
/// </summary>
public sealed class VoiceService : IDisposable
{
    private readonly SynchronizationContext? _context;
    private SpeechSynthesizer? _synth;
    private SpeechRecognitionEngine? _recognizer;
    private bool _speaking;
    private bool _listening;
    private bool _disposed;

    /// <summary>True when a SpeechSynthesizer could be created (SAPI present).</summary>
    public bool IsAvailable { get; private set; }

    /// <summary>True when a speech recognizer could be created with a working audio input.</summary>
    public bool IsRecognizerAvailable { get; private set; }

    /// <summary>Culture the recognizer actually uses ("en-GB", "en-US", …), or null when none exists.</summary>
    public string? RecognizerCultureName { get; private set; }

    public bool IsSpeaking => _speaking;
    public bool IsListening => _listening;

    public event Action? SpeakingStarted;
    public event Action? SpeakingFinished;
    public event Action? ListeningStarted;
    public event Action? ListeningStopped;
    public event Action<string>? Recognized;

    public VoiceService()
    {
        _context = SynchronizationContext.Current;
        try
        {
            _synth = new SpeechSynthesizer();
            _synth.SpeakStarted += (_, _) => Post(OnSpeakStarted);
            _synth.SpeakCompleted += (_, e) => Post(() => OnSpeakCompleted(e));
            IsAvailable = true;
        }
        catch (Exception)
        {
            // No SAPI on this machine: voice output stays unavailable.
            _synth = null;
            IsAvailable = false;
        }

        try
        {
            var recognizers = SpeechRecognitionEngine.InstalledRecognizers();
            var culture = PickRecognizerCulture(recognizers);
            if (culture is null)
            {
                IsRecognizerAvailable = false;
            }
            else
            {
                _recognizer = new SpeechRecognitionEngine(culture);
                _recognizer.SetInputToDefaultAudioDevice();
                _recognizer.LoadGrammar(new DictationGrammar());
                _recognizer.SpeechRecognized += (_, e) => Post(() => OnSpeechRecognized(e));
                _recognizer.RecognizeCompleted += (_, _) => Post(OnRecognizeCompleted);
                RecognizerCultureName = culture.Name;
                IsRecognizerAvailable = true;
            }
        }
        catch (Exception)
        {
            // No recognizer, or no working microphone: input stays unavailable.
            _recognizer = null;
            IsRecognizerAvailable = false;
        }
    }

    /// <summary>Installed TTS voices, mapped to the Core DTOs (female en-GB first for display).</summary>
    public IReadOnlyList<CoreVoiceInfo> InstalledVoices
    {
        get
        {
            if (_synth is null) return Array.Empty<CoreVoiceInfo>();
            try
            {
                var mapped = _synth.GetInstalledVoices()
                    .Select(iv => new CoreVoiceInfo(
                        iv.VoiceInfo.Name,
                        iv.VoiceInfo.Culture.Name,
                        iv.VoiceInfo.Gender switch
                        {
                            System.Speech.Synthesis.VoiceGender.Female => Athena.Core.VoiceGender.Female,
                            System.Speech.Synthesis.VoiceGender.Male => Athena.Core.VoiceGender.Male,
                            System.Speech.Synthesis.VoiceGender.Neutral => Athena.Core.VoiceGender.Neutral,
                            _ => Athena.Core.VoiceGender.Unknown,
                        }))
                    .ToList();
                return VoiceSelection.OrderForDisplay(mapped);
            }
            catch (Exception)
            {
                return Array.Empty<CoreVoiceInfo>();
            }
        }
    }

    /// <summary>Apply saved preferences: pick the voice by policy, set rate and volume.</summary>
    public void ApplySettings(VoiceSettings settings)
    {
        if (_synth is null) return;
        try
        {
            var pick = VoiceSelection.Pick(InstalledVoices, settings.VoiceName);
            if (pick is not null)
                _synth.SelectVoice(pick.Name);
            _synth.Rate = settings.Rate;
            _synth.Volume = settings.Volume;
        }
        catch (Exception)
        {
            // A voice that vanished between sessions must not break startup.
        }
    }

    /// <summary>Speak text asynchronously. Returns false when speech output is unavailable.</summary>
    public bool Speak(string text)
    {
        if (_synth is null || string.IsNullOrWhiteSpace(text)) return false;
        try
        {
            _synth.SpeakAsyncCancelAll();
            _synth.SpeakAsync(text);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>Interrupt any speech in progress.</summary>
    public void StopSpeaking()
    {
        if (_synth is null) return;
        try
        {
            _synth.SpeakAsyncCancelAll();
        }
        catch (Exception)
        {
            // Already stopped or gone; nothing to do.
        }
        if (_speaking)
        {
            _speaking = false;
            Post(() => SpeakingFinished?.Invoke());
        }
    }

    /// <summary>Start listening for one utterance. Returns false when the mic/recognizer is unavailable.</summary>
    public bool StartListening()
    {
        if (_recognizer is null || !IsRecognizerAvailable || _listening) return false;
        try
        {
            _recognizer.RecognizeAsync(RecognizeMode.Multiple);
            _listening = true;
            Post(() => ListeningStarted?.Invoke());
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public void StopListening()
    {
        if (_recognizer is null || !_listening) return;
        try
        {
            _recognizer.RecognizeAsyncStop();
        }
        catch (Exception)
        {
            // Already stopped; nothing to do.
        }
        _listening = false;
        Post(() => ListeningStopped?.Invoke());
    }

    private void OnSpeakStarted()
    {
        _speaking = true;
        SpeakingStarted?.Invoke();
    }

    private void OnSpeakCompleted(SpeakCompletedEventArgs e)
    {
        // CancelAll fires Completed for the cancelled prompt too;
        // only clear the flag when nothing is speaking any more.
        if (_synth is not null && _synth.State == SynthesizerState.Speaking) return;
        if (!_speaking) return;
        _speaking = false;
        SpeakingFinished?.Invoke();
    }

    private void OnSpeechRecognized(SpeechRecognizedEventArgs e)
    {
        var text = e.Result.Text.Trim();
        StopListening();
        if (!string.IsNullOrWhiteSpace(text))
            Recognized?.Invoke(text);
    }

    private void OnRecognizeCompleted()
    {
        if (!_listening) return;
        _listening = false;
        ListeningStopped?.Invoke();
    }

    private static CultureInfo? PickRecognizerCulture(
        IReadOnlyList<RecognizerInfo> recognizers)
    {
        if (recognizers.Count == 0) return null;
        var gb = recognizers.FirstOrDefault(r =>
            r.Culture.Name.Equals("en-GB", StringComparison.OrdinalIgnoreCase));
        if (gb is not null) return gb.Culture;
        var us = recognizers.FirstOrDefault(r =>
            r.Culture.Name.Equals("en-US", StringComparison.OrdinalIgnoreCase));
        if (us is not null) return us.Culture;
        return recognizers[0].Culture;
    }

    private void Post(Action action)
    {
        if (_context is not null)
            _context.Post(_ => action(), null);
        else
            action();
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        try { _recognizer?.RecognizeAsyncStop(); } catch (Exception) { }
        try { _recognizer?.Dispose(); } catch (Exception) { }
        try { _synth?.SpeakAsyncCancelAll(); } catch (Exception) { }
        try { _synth?.Dispose(); } catch (Exception) { }
    }
}
