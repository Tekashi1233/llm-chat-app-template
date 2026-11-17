using System.Net;
using System.Text;
using Athena.Core;

// N1 test harness (console app, no test framework needed — the project
// has no NuGet access to test SDKs on this VM).
//   dotnet run --project tests/Athena.Tests
// Live-backend tests run only when ATHENA_TEST_KEY is set; they use a
// throwaway conversation id and make normal application requests.
var failures = new List<string>();
var passes = 0;

void Check(string name, bool ok, string detail = "")
{
    if (ok)
    {
        passes++;
        Console.WriteLine($"PASS {name}");
    }
    else
    {
        failures.Add(name);
        Console.WriteLine($"FAIL {name} {detail}");
    }
}

void Eq(string name, object? got, object? want) =>
    Check(name, Equals(got?.ToString(), want?.ToString()),
        $"got=[{got}] want=[{want}]");

// ---------- SseParser ----------
Eq("sse: two delta chunks joined",
    SseParser.Parse(
        "data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\n" +
        "data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n" +
        "data: [DONE]\n"),
    "Hello");
Eq("sse: message.content fallback",
    SseParser.Parse(
        "data: {\"choices\":[{\"message\":{\"content\":\"Hi there\"}}]}\n" +
        "data: [DONE]\n"),
    "Hi there");
Eq("sse: top-level response fallback",
    SseParser.Parse("data: {\"response\":\"Yo\"}\n"),
    "Yo");
Eq("sse: empty stream",
    SseParser.Parse("data: [DONE]\n"),
    "");
Eq("sse: malformed chunk skipped, rest kept",
    SseParser.Parse(
        "data: {not json\n" +
        "data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n"),
    "ok");

// ---------- MessageValidation ----------
Check("validation: normal text valid",
    MessageValidation.IsValidUserMessage("hello"));
Check("validation: whitespace-only invalid",
    !MessageValidation.IsValidUserMessage("   "));
Check("validation: empty invalid",
    !MessageValidation.IsValidUserMessage(""));
Check("validation: null invalid",
    !MessageValidation.IsValidUserMessage(null));

// ---------- KeyValidator with a mocked HTTP layer ----------
static HttpClient MockClient(HttpStatusCode status, string body = "{}")
{
    var handler = new MockHandler(status, body);
    return new HttpClient(handler)
    {
        BaseAddress = new Uri(AthenaApi.WorkerBase),
    };
}

var mock400 = new ChatClient(MockClient(HttpStatusCode.BadRequest));
Eq("key-check: 400 means valid",
    await mock400.ValidateKeyAsync("any-key"), KeyValidationResult.Valid);
var mock401 = new ChatClient(MockClient(HttpStatusCode.Unauthorized));
Eq("key-check: 401 means invalid",
    await mock401.ValidateKeyAsync("bad-key"), KeyValidationResult.Invalid);
var mock429 = new ChatClient(MockClient((HttpStatusCode)429));
Eq("key-check: 429 means rate-limited",
    await mock429.ValidateKeyAsync("any-key"), KeyValidationResult.RateLimited);

// SendMessageAsync with a mocked 200 SSE response.
var mockChat = new ChatClient(MockClient(HttpStatusCode.OK,
    "data: {\"choices\":[{\"delta\":{\"content\":\"4\"}}]}\n" +
    "data: [DONE]\n"));
Eq("chat: mocked SSE reply parsed",
    await mockChat.SendMessageAsync("What is 2+2?", "k", "conv-test"), "4");

// Empty message is rejected before any HTTP call.
var counting = new CountingHandler();
var countingClient = new ChatClient(new HttpClient(counting)
{
    BaseAddress = new Uri(AthenaApi.WorkerBase),
});
var threw = false;
try { await countingClient.SendMessageAsync("   ", "k", "conv-test"); }
catch (ArgumentException) { threw = true; }
Check("chat: empty message rejected client-side, no HTTP call",
    threw && counting.Calls == 0);

// ---------- DPAPI (Windows-only) ----------
if (OperatingSystem.IsWindows())
{
    SecureStore.SaveKey("n1-test-key-roundtrip");
    Eq("dpapi: key round-trips",
        SecureStore.TryLoadKey(), "n1-test-key-roundtrip");
}
else
{
    Console.WriteLine("SKIP dpapi: Windows-only (run on the laptop to verify).");
}

// ---------- Live backend (only with ATHENA_TEST_KEY) ----------
var liveKey = Environment.GetEnvironmentVariable("ATHENA_TEST_KEY");
if (!string.IsNullOrWhiteSpace(liveKey))
{
    var live = new ChatClient();
    Eq("live: key probe returns Valid",
        await live.ValidateKeyAsync(liveKey), KeyValidationResult.Valid);
    Eq("live: wrong key returns Invalid",
        await live.ValidateKeyAsync(new string('0', 64)),
        KeyValidationResult.Invalid);
    var reply = await live.SendMessageAsync(
        "What is 2+2? Answer with just the number.",
        liveKey, "n1-live-test");
    Check("live: chat round-trip answers",
        reply.Contains('4'), $"reply=[{reply}]");
}
else
{
    Console.WriteLine("SKIP live: set ATHENA_TEST_KEY to run backend tests.");
}

Console.WriteLine($"\n{passes} passed, {failures.Count} failed");
return failures.Count == 0 ? 0 : 1;

// ---------- helpers ----------
sealed class MockHandler : HttpMessageHandler
{
    private readonly HttpStatusCode _status;
    private readonly string _body;
    public MockHandler(HttpStatusCode status, string body)
    {
        _status = status;
        _body = body;
    }
    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken ct) =>
        Task.FromResult(new HttpResponseMessage(_status)
        {
            Content = new StringContent(_body, Encoding.UTF8, "application/json"),
        });
}

sealed class CountingHandler : HttpMessageHandler
{
    public int Calls;
    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken ct)
    {
        Calls++;
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("data: [DONE]\n", Encoding.UTF8,
                "text/event-stream"),
        });
    }
}
