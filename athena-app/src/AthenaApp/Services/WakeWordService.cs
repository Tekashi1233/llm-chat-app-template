using System.Globalization;
using System.Speech.Recognition;
using Athena.Core;

namespace Athena;

/// <summary>
/// N7 wake-word listener: a dedicated SpeechRecognitionEngine
/// with a one-word grammar ("athena"), listening continuously
/// with RecognizeMode.Multiple. Saying "athena" raises
/// WakeWordHeard — MainWindow just shows the window; the word
/// never sends anything.
///
/// Wake-word heard is gated by the sibling Core policy
/// (Athena.Core.WakeWordTrigger: exact word "athena" after
/// normalization, confidence &gt;= 0.7, 3s cooldown since the
/// last accepted trigger) — one source of truth, shared with
/// the Linux test harness. The SAPI grammar only ever emits
/// the single word anyway; the policy is defense-in-depth.
///
/// Every SAPI call is guarded, like VoiceService: a machine
/// without SAPI, without a recognizer, or without a mic gets
/// IsAvailable == false and no listener, never a crash.
/// Callbacks are marshalled to the synchronization context
/// captured at construction (the UI thread), same Post()
/// pattern as VoiceService.
/// </summary>
public sealed class WakeWordService : IDisposable
{
    private readonly SynchronizationContext? _context;
    private SpeechRecognitionEngine? _recognizer;
    private readonly WakeWordTrigger _trigger = new();
    private bool _listening;
    private bool _disposed;

    /// <summary>True when a recognizer could be created with a working audio input.</summary>
    public bool IsAvailable { get; private set; }

    /// <summary>Culture the recognizer actually uses ("en-GB", "en-US", …), or null when none exists.</summary>
    public string? RecognizerCultureName { get; private set; }

    public bool IsListening => _listening;

    /// <summary>Raised (on the UI thread) when "athena" is heard and passes the gate.</summary>
    public event Action? WakeWordHeard;

    public WakeWordService()
    {
        _context = SynchronizationContext.Current;
        try
        {
            var recognizers = SpeechRecognitionEngine.InstalledRecognizers();
            var culture = PickRecognizerCulture(recognizers);
            if (culture is null)
            {
                IsAvailable = false;
            }
            else
            {
                _recognizer = new SpeechRecognitionEngine(culture);
                _recognizer.SetInputToDefaultAudioDevice();
                var grammar = new Grammar(new GrammarBuilder(new Choices("athena")));
                _recognizer.LoadGrammar(grammar);
                _recognizer.SpeechRecognized += (_, e) => Post(() => OnSpeechRecognized(e));
                _recognizer.RecognizeCompleted += (_, _) => Post(OnRecognizeCompleted);
                RecognizerCultureName = culture.Name;
                IsAvailable = true;
            }
        }
        catch (Exception)
        {
            // No recognizer, or no working microphone: wake word stays unavailable.
            _recognizer = null;
            IsAvailable = false;
        }
    }

    /// <summary>Start continuous listening. Returns false when the recognizer is unavailable.</summary>
    public bool Start()
    {
        if (_recognizer is null || !IsAvailable || _disposed) return false;
        if (_listening) return true;
        try
        {
            _recognizer.RecognizeAsync(RecognizeMode.Multiple);
            _listening = true;
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public void Stop()
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
    }

    private void OnSpeechRecognized(SpeechRecognizedEventArgs e)
    {
        var text = e.Result.Text.Trim();
        // Single gate: the Core policy (exact word, confidence
        // >= 0.7, 3s cooldown). It also owns the cooldown clock,
        // so a rejected result never resets it.
        if (!_trigger.TryTrigger(text, e.Result.Confidence, DateTimeOffset.UtcNow)) return;
        WakeWordHeard?.Invoke();
    }

    private void OnRecognizeCompleted()
    {
        // With RecognizeMode.Multiple this only fires when the engine
        // actually stopped (audio device lost, or Stop()). Do not
        // auto-restart: the listener stays honestly off until
        // MainWindow starts it again (toggle / next launch).
        if (!_listening) return;
        _listening = false;
    }

    /// <summary>Same culture preference as VoiceService: en-GB, then en-US, then first installed.</summary>
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
        _listening = false;
        try { _recognizer?.RecognizeAsyncStop(); } catch (Exception) { }
        try { _recognizer?.Dispose(); } catch (Exception) { }
    }
}
