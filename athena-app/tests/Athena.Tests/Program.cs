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

// ---------- N2: ConversationStore ----------
var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
BackupMessage Msg(long id, string conv, string role, string content, long ms) =>
    new(id, conv, role, content, ms, "user");
var storeMsgs = new[]
{
    Msg(1, "c-old", "user", "Hello, who are you?", nowMs - 86_400_000),
    Msg(2, "c-old", "assistant", "I am ATHENA.", nowMs - 86_400_000 + 1000),
    Msg(3, "c-new", "user", "What is the weather in Riyadh today please?", nowMs - 60_000),
    Msg(4, "c-new", "assistant", "Clear, 22 degrees.", nowMs - 59_000),
    Msg(5, "c-new", "user", "And tomorrow?", nowMs - 30_000),
};
var grouped = ConversationStore.GroupMessages(storeMsgs);
Eq("conversations: two groups, newest first", grouped.Count, 2);
Eq("conversations: newest is c-new", grouped[0].Id, "c-new");
Eq("conversations: title is first user message", grouped[0].Title,
    "What is the weather in Riyadh today please?");
Eq("conversations: message count", grouped[0].MessageCount, 3);
Eq("conversations: old title", grouped[1].Title, "Hello, who are you?");
Check("conversations: search finds title",
    ConversationStore.Search(grouped, "weather").Count == 1);
Check("conversations: search is case-insensitive",
    ConversationStore.Search(grouped, "RIYADH").Count == 1);
Check("conversations: empty query returns all",
    ConversationStore.Search(grouped, "").Count == 2);
Check("conversations: no match returns none",
    ConversationStore.Search(grouped, "zzz-no-such-thing").Count == 0);
Eq("conversations: title truncates long first lines",
    ConversationStore.TruncateTitle(new string('x', 60)).Length, 48);
Check("time: just now", ConversationStore.RelativeTime(DateTimeOffset.Now) == "just now");
Check("time: minutes ago",
    ConversationStore.RelativeTime(DateTimeOffset.Now.AddMinutes(-5)) == "5 minutes ago");
Check("time: hours ago",
    ConversationStore.RelativeTime(DateTimeOffset.Now.AddHours(-3)) == "3 hours ago");
Check("time: yesterday",
    ConversationStore.RelativeTime(DateTimeOffset.Now.AddDays(-1)) == "Yesterday");

// ---------- N2: ToolCatalog ----------
Check("tools: 13 registered tools", ToolCatalog.All.Length == 13);
Check("tools: names unique",
    ToolCatalog.All.Select(t => t.Name).Distinct().Count() == 13);
Check("tools: remember_fact present",
    ToolCatalog.All.Any(t => t.Name == "remember_fact"));
Check("tools: every tool has a description",
    ToolCatalog.All.All(t => !string.IsNullOrWhiteSpace(t.Description)));

// ---------- N2: DataClient with mocked HTTP ----------
var mockInfo = new DataClient(MockClient(HttpStatusCode.OK,
    "{\"assistant\":\"ATHENA\",\"provider\":\"Cloudflare Workers AI\"," +
    "\"configured_model\":\"@cf/openai/gpt-oss-20b\",\"reasoning_effort\":\"medium\"," +
    "\"streaming\":true,\"tools\":[\"get_weather\",\"remember_fact\"]," +
    "\"auth\":\"x-vesper-key\",\"memory\":\"d1\",\"status\":\"configured\"}"));
var info = await mockInfo.GetModelInfoAsync();
Eq("data: model-info assistant", info.Assistant, "ATHENA");
Eq("data: model-info tools parsed", info.Tools.Length, 2);
Check("data: remember_fact registered in model-info",
    ToolCatalog.All.First(t => t.Name == "remember_fact").IsRegistered(info));

var mockBackup = new DataClient(MockClient(HttpStatusCode.OK,
    "{\"exported_at\":\"2026-11-17T12:00:00.000Z\",\"count\":2,\"messages\":[" +
    "{\"id\":1,\"conversation_id\":\"c1\",\"role\":\"user\",\"content\":\"hi\",\"created_at\":1794960000000,\"origin\":\"user\"}," +
    "{\"id\":2,\"conversation_id\":\"c1\",\"role\":\"assistant\",\"content\":\"hello\",\"created_at\":1794960001000,\"origin\":\"server\"}]}"));
var backup = await mockBackup.GetBackupAsync("k");
Eq("data: backup count", backup.Count, 2);
Eq("data: backup message role", backup.Messages[0].Role, "user");
Eq("data: backup created_at parses",
    backup.Messages[0].CreatedAt.ToString("yyyy"), "2026");

var mock404 = new DataClient(MockClient(HttpStatusCode.NotFound));
Check("data: memories 404 (endpoint not deployed) returns null",
    await mock404.GetMemoriesAsync("k", "c1") is null);
Check("data: device status 404 returns null",
    await mock404.GetDeviceStatusAsync("k") is null);
Check("data: device activity 404 returns null",
    await mock404.GetDeviceActivityAsync("k") is null);

var mockMem = new DataClient(MockClient(HttpStatusCode.OK,
    "{\"conversation_id\":\"c1\",\"count\":1,\"memories\":[" +
    "{\"id\":7,\"kind\":\"fact\",\"topic\":\"favourite colour\",\"fact\":\"Crimson.\"," +
    "\"project\":null,\"source\":\"user\",\"created_at\":1794960000000," +
    "\"updated_at\":1794960000000,\"superseded_by\":null,\"status\":\"active\"}]}"));
var mems = await mockMem.GetMemoriesAsync("k", "c1");
Check("data: memories parse", mems?.Memories.Length == 1);
Eq("data: memory topic", mems?.Memories[0].Topic, "favourite colour");
Eq("data: memory source label", mems?.Memories[0].SourceLabel,
    "You told ATHENA directly");

var mockDev = new DataClient(MockClient(HttpStatusCode.OK,
    "{\"online\":true,\"device\":\"DESKTOP-9NRU515\",\"last_seen_seconds_ago\":9," +
    "\"cpu_percent\":15.9,\"ram_used_gb\":6.9,\"ram_total_gb\":11.9," +
    "\"disks\":[{\"drive\":\"C:\",\"free_gb\":109.2,\"total_gb\":237.8}]," +
    "\"network\":{\"interface\":\"Wi-Fi\",\"down_mbps\":48.0,\"up_mbps\":12.0}," +
    "\"uptime_hours\":3.8}"));
var dev = await mockDev.GetDeviceStatusAsync("k");
Check("data: device online", dev?.Online == true);
Eq("data: device cpu", dev?.CpuPercent, 15.9);
Eq("data: device disk free", dev?.Disks?[0].FreeGb, 109.2);

var mockAct = new DataClient(MockClient(HttpStatusCode.OK,
    "{\"device\":\"DESKTOP-9NRU515\",\"count\":1,\"actions\":[" +
    "{\"kind\":\"open_app\",\"detail\":\"Opened Notepad.\",\"approved\":1," +
    "\"created_at\":1794960000000}]}"));
var acts = await mockAct.GetDeviceActivityAsync("k");
Check("data: activity parses", acts?.Actions.Length == 1);
Check("data: activity approved", acts?.Actions[0].WasApproved == true);

// ---------- N3: Presence state machine (was the Island) ----------
var machine = new PresenceStateMachine();
Eq("presence: starts Idle", machine.Current.State, PresenceState.Idle);
Check("presence: Chat can set Thinking",
    machine.TrySet(PresenceState.Thinking, PresenceSource.Chat, "Thinking…"));
Check("presence: System cannot clear Thinking to Idle",
    !machine.TrySet(PresenceState.Idle, PresenceSource.System, "Ready"));
Check("presence: Chat completes Thinking to Idle",
    machine.TrySet(PresenceState.Idle, PresenceSource.Chat, "Idle"));
Check("presence: Chat cannot set Listening (needs voice, N4)",
    !machine.TrySet(PresenceState.Listening, PresenceSource.Chat, "Listening"));
Check("presence: Voice can set Listening",
    machine.TrySet(PresenceState.Listening, PresenceSource.Voice, "Listening"));
Check("presence: Chat cannot set Speaking",
    !machine.TrySet(PresenceState.Speaking, PresenceSource.Chat, "Speaking"));
Check("presence: Chat cannot set Working (needs device, N5)",
    !machine.TrySet(PresenceState.Working, PresenceSource.Chat, "Working"));
Check("presence: Device can set Working",
    machine.TrySet(PresenceState.Working, PresenceSource.Device, "Working"));
Check("presence: Chat cannot set Approval",
    !machine.TrySet(PresenceState.Approval, PresenceSource.Chat, "Approval"));
Check("presence: anyone can set Offline",
    machine.TrySet(PresenceState.Offline, PresenceSource.System, "Offline"));
Check("presence: Offline clears to Idle",
    machine.TrySet(PresenceState.Idle, PresenceSource.System, "Ready"));
Check("presence: same-state updates detail",
    machine.TrySet(PresenceState.Idle, PresenceSource.System, "Ready again")
    && machine.Current.Detail == "Ready again");
Check("presence: Error from Chat",
    machine.TrySet(PresenceState.Thinking, PresenceSource.Chat, "Thinking…")
    && machine.TrySet(PresenceState.Error, PresenceSource.Chat, "Failed"));
Eq("presence: Error state held", machine.Current.State, PresenceState.Error);

// ---------- N4: Voice selection policy ----------
var fakeVoices = new[]
{
    new VoiceInfo("Microsoft David Desktop", "en-US", VoiceGender.Male),
    new VoiceInfo("Microsoft Hazel Desktop", "en-GB", VoiceGender.Female),
    new VoiceInfo("Microsoft George", "en-GB", VoiceGender.Male),
    new VoiceInfo("Microsoft Sonia Online", "en-GB", VoiceGender.Female),
};
Eq("voice: prefers Hazel over other en-GB voices",
    VoiceSelection.Pick(fakeVoices, null)?.Name, "Microsoft Hazel Desktop");
Eq("voice: saved name wins when installed",
    VoiceSelection.Pick(fakeVoices, "Microsoft Sonia Online")?.Name, "Microsoft Sonia Online");
Eq("voice: saved name ignored when not installed",
    VoiceSelection.Pick(fakeVoices, "No Such Voice")?.Name, "Microsoft Hazel Desktop");
var noHazel = new[]
{
    new VoiceInfo("Microsoft David Desktop", "en-US", VoiceGender.Male),
    new VoiceInfo("Microsoft George", "en-GB", VoiceGender.Male),
    new VoiceInfo("Microsoft Sonia Online", "en-GB", VoiceGender.Female),
};
Eq("voice: female en-GB when no Hazel",
    VoiceSelection.Pick(noHazel, null)?.Name, "Microsoft Sonia Online");
var maleGbOnly = new[]
{
    new VoiceInfo("Microsoft David Desktop", "en-US", VoiceGender.Male),
    new VoiceInfo("Microsoft George", "en-GB", VoiceGender.Male),
};
Eq("voice: any en-GB when no female en-GB",
    VoiceSelection.Pick(maleGbOnly, null)?.Name, "Microsoft George");
var femaleUsOnly = new[]
{
    new VoiceInfo("Microsoft Zira Desktop", "en-US", VoiceGender.Female),
    new VoiceInfo("Microsoft David Desktop", "en-US", VoiceGender.Male),
};
Eq("voice: any female voice when no en-GB",
    VoiceSelection.Pick(femaleUsOnly, null)?.Name, "Microsoft Zira Desktop");
var maleUsOnly = new[]
{
    new VoiceInfo("Microsoft David Desktop", "en-US", VoiceGender.Male),
};
Eq("voice: first voice as system default fallback",
    VoiceSelection.Pick(maleUsOnly, null)?.Name, "Microsoft David Desktop");
Check("voice: no voices returns null",
    VoiceSelection.Pick(Array.Empty<VoiceInfo>(), null) is null);
var ordered = VoiceSelection.OrderForDisplay(fakeVoices);
Eq("voice: display order puts female en-GB first",
    ordered[0].Name, "Microsoft Hazel Desktop");
Check("voice: display order puts en-GB before en-US",
    ordered[^1].CultureName == "en-US");
Check("voice: en-GB detection",
    VoiceSelection.IsEnGb("en-GB") && VoiceSelection.IsEnGb("en-GB-x1")
    && !VoiceSelection.IsEnGb("en-US"));

// ---------- N4: Voice settings round-trip ----------
var tmpVoiceDir = Path.Combine(
    Path.GetTempPath(), "athena-voice-test-" + Guid.NewGuid().ToString("N"));
Check("voice settings: missing file returns null",
    VoiceSettingsStore.Load(tmpVoiceDir) is null);
var savedSettings = new VoiceSettings(
    OutputEnabled: true, VoiceName: "Microsoft Hazel Desktop",
    Rate: -2, Volume: 80, MicEnabled: false, RecognitionCulture: "en-GB");
VoiceSettingsStore.Save(tmpVoiceDir, savedSettings);
var loadedSettings = VoiceSettingsStore.Load(tmpVoiceDir);
Check("voice settings: round-trip",
    loadedSettings is not null && loadedSettings.OutputEnabled
    && loadedSettings.VoiceName == "Microsoft Hazel Desktop"
    && loadedSettings.Rate == -2 && loadedSettings.Volume == 80
    && !loadedSettings.MicEnabled
    && loadedSettings.RecognitionCulture == "en-GB");
var outOfRange = new VoiceSettings(
    OutputEnabled: false, VoiceName: null, Rate: 99,
    Volume: 500, MicEnabled: true, RecognitionCulture: "");
var clampedSettings = outOfRange.Clamped();
Check("voice settings: rate/volume clamped",
    clampedSettings.Rate == 10 && clampedSettings.Volume == 100
    && clampedSettings.RecognitionCulture == "en-GB");
Check("voice settings: defaults are output-off",
    VoiceSettings.Defaults is { OutputEnabled: false, Rate: 0, Volume: 100, MicEnabled: true });
File.WriteAllText(Path.Combine(tmpVoiceDir, "voice.json"), "{ not json");
Check("voice settings: corrupt file returns null",
    VoiceSettingsStore.Load(tmpVoiceDir) is null);
Directory.Delete(tmpVoiceDir, recursive: true);

// ---------- N4: Voice-source Presence transitions ----------
var voiceMachine = new PresenceStateMachine();
Check("voice presence: Voice can set Listening",
    voiceMachine.TrySet(PresenceState.Listening, PresenceSource.Voice, "Listening…"));
Check("voice presence: Voice can set Speaking from Listening",
    voiceMachine.TrySet(PresenceState.Speaking, PresenceSource.Voice, "Speaking…"));
Check("voice presence: Speaking returns to Idle",
    voiceMachine.TrySet(PresenceState.Idle, PresenceSource.Voice, "Idle"));
Check("voice presence: Chat cannot set Speaking",
    !voiceMachine.TrySet(PresenceState.Speaking, PresenceSource.Chat, "Speaking…"));
Check("voice presence: System cannot set Listening",
    !voiceMachine.TrySet(PresenceState.Listening, PresenceSource.System, "Listening…"));
Check("voice presence: Voice can set Offline",
    voiceMachine.TrySet(PresenceState.Offline, PresenceSource.Voice, "Offline"));

// ---------- N5 M1: device quick actions + reply signals ----------
Eq("m1: four approved quick actions", DeviceQuickActions.All.Length, 4);
Check("m1: quick-action labels are unique",
    DeviceQuickActions.All.Select(a => a.Label).Distinct().Count() == 4);
Check("m1: quick-action prompts are non-empty and unique",
    DeviceQuickActions.All.All(a => !string.IsNullOrWhiteSpace(a.Prompt))
    && DeviceQuickActions.All.Select(a => a.Prompt).Distinct().Count() == 4);
Check("m1: every listed prompt is approved",
    DeviceQuickActions.All.All(a => DeviceQuickActions.IsApprovedPrompt(a.Prompt)));
Check("m1: unlisted prompt is not approved",
    !DeviceQuickActions.IsApprovedPrompt("Format the C: drive")
    && !DeviceQuickActions.IsApprovedPrompt("")
    && !DeviceQuickActions.IsApprovedPrompt("Ping google.com "));
Check("m1: approval-waiting reply detected",
    DeviceReplySignals.IsApprovalWaiting(
        "The command was sent to the laptop and a confirmation popup is waiting on its screen. " +
        "Tell the user to click Allow on the popup, then ask me again and I will check whether it went through."));
Check("m1: ordinary reply is not approval-waiting",
    !DeviceReplySignals.IsApprovalWaiting("I am ATHENA, your personal AI assistant.")
    && !DeviceReplySignals.IsApprovalWaiting("")
    && !DeviceReplySignals.IsApprovalWaiting(null));

// ---------- N5 M1: Device-source Presence transitions ----------
var deviceMachine = new PresenceStateMachine();
Check("m1 presence: Chat sets Thinking",
    deviceMachine.TrySet(PresenceState.Thinking, PresenceSource.Chat, "Thinking…"));
Check("m1 presence: Device can set Working during a chat turn",
    deviceMachine.TrySet(PresenceState.Working, PresenceSource.Device, "Working on your laptop…"));
Eq("m1 presence: Working state held", deviceMachine.Current.State, PresenceState.Working);
Check("m1 presence: Chat clears Working to Idle",
    deviceMachine.TrySet(PresenceState.Idle, PresenceSource.Chat, "Idle"));
Check("m1 presence: Device can set Approval",
    deviceMachine.TrySet(PresenceState.Approval, PresenceSource.Device, "Waiting for approval"));
Check("m1 presence: System cannot set Working",
    !deviceMachine.TrySet(PresenceState.Working, PresenceSource.System, "Working…"));
Check("m1 presence: Chat cannot set Working",
    !deviceMachine.TrySet(PresenceState.Working, PresenceSource.Chat, "Working…"));

// ---------- N5 M2: owner auth (password + session tokens) ----------
// Header selection: token always wins; key is the grace fallback.
Eq("m2: token preferred over key",
    AuthHeaders.Select("tok-1", "key-1").Name, "Authorization");
Eq("m2: token header value is Bearer",
    AuthHeaders.Select("tok-1", "key-1").Value, "Bearer tok-1");
Eq("m2: key used when no token",
    AuthHeaders.Select(null, "key-1").Name, "X-Vesper-Key");
Eq("m2: key header value is the key",
    AuthHeaders.Select(null, "key-1").Value, "key-1");
Check("m2: PrefersToken true only with a token",
    AuthHeaders.PrefersToken("tok") && !AuthHeaders.PrefersToken(null)
    && !AuthHeaders.PrefersToken("   "));
var noAuthThrew = false;
try { AuthHeaders.Select(null, null); } catch (ArgumentException) { noAuthThrew = true; }
Check("m2: no token and no key throws", noAuthThrew);

// Password rules: minimum 8, confirm must match exactly.
Check("m2: password of 8 is valid",
    AuthValidation.IsValidPassword("12345678"));
Check("m2: password of 7 is invalid",
    !AuthValidation.IsValidPassword("1234567"));
Check("m2: empty/null password invalid",
    !AuthValidation.IsValidPassword("") && !AuthValidation.IsValidPassword(null));
Check("m2: matching passwords match",
    AuthValidation.PasswordsMatch("secret-1", "secret-1"));
Check("m2: different passwords do not match",
    !AuthValidation.PasswordsMatch("secret-1", "secret-2"));

// Migration: exchange only when a key is stored and no token yet.
Check("m2: migrate when key stored, no token",
    AuthValidation.ShouldMigrate(hasToken: false, hasKey: true));
Check("m2: no migrate when token already stored",
    !AuthValidation.ShouldMigrate(hasToken: true, hasKey: true));
Check("m2: no migrate when nothing stored",
    !AuthValidation.ShouldMigrate(hasToken: false, hasKey: false));

// via_voice tagging: present only for mic messages, never for typed.
var micMsg = ChatMessageBuilder.UserMessage("wolf", viaVoice: true);
Check("m2: mic message carries via_voice",
    micMsg.TryGetValue("via_voice", out var vv) && vv is true);
var typedMsg = ChatMessageBuilder.UserMessage("wolf", viaVoice: false);
Check("m2: typed message has NO via_voice key",
    !typedMsg.ContainsKey("via_voice"));
Eq("m2: message content trimmed",
    ChatMessageBuilder.UserMessage("  hello  ", viaVoice: false)["content"], "hello");

// AuthClient with mocked HTTP.
var mockStatus = new AuthClient(MockClient(HttpStatusCode.OK, "{\"setup_required\":true}"));
Eq("m2: status setup_required parses",
    (await mockStatus.GetStatusAsync())?.SetupRequired, true);
var mockStatus404 = new AuthClient(MockClient(HttpStatusCode.NotFound));
Check("m2: status 404 (endpoints not deployed) returns null",
    await mockStatus404.GetStatusAsync() is null);
var mockLogin = new AuthClient(MockClient(HttpStatusCode.OK,
    "{\"token\":\"tok-abc\",\"expires_at\":\"2026-12-17T00:00:00.000Z\"}"));
var loginResp = await mockLogin.LoginAsync("correct-password");
Eq("m2: login returns the token", loginResp.Token, "tok-abc");
Eq("m2: login returns expiry", loginResp.ExpiresAt, "2026-12-17T00:00:00.000Z");
var mockLogin401 = new AuthClient(MockClient(HttpStatusCode.Unauthorized));
var login401 = false;
try { await mockLogin401.LoginAsync("wrong-password"); }
catch (AthenaApiException ex) { login401 = ex.StatusCode == 401; }
Check("m2: wrong password answers 401", login401);
var mockSetup409 = new AuthClient(MockClient(HttpStatusCode.Conflict));
var setup409 = false;
try { await mockSetup409.SetupAsync("new-password-1", "site-key"); }
catch (AthenaApiException ex) { setup409 = ex.StatusCode == 409; }
Check("m2: setup when owner exists answers 409", setup409);
var mockSetupShort = new AuthClient(MockClient(HttpStatusCode.OK, "{}"));
var setupShort = false;
try { await mockSetupShort.SetupAsync("short", "site-key"); }
catch (ArgumentException) { setupShort = true; }
Check("m2: setup rejects a short password client-side, no HTTP call", setupShort);
var mockExchange = new AuthClient(MockClient(HttpStatusCode.OK,
    "{\"token\":\"tok-ex\",\"expires_at\":\"2026-12-17T00:00:00.000Z\"}"));
Eq("m2: exchange swaps a key for a token",
    (await mockExchange.ExchangeAsync("site-key")).Token, "tok-ex");
var mockAuthValid = new AuthClient(MockClient(HttpStatusCode.BadRequest));
Eq("m2: token probe 400 means valid",
    await mockAuthValid.ValidateAuthAsync("tok-1", null), KeyValidationResult.Valid);

// ChatClient: Bearer header + via_voice body, captured from the wire.
var capToken = new CaptureHandler(HttpStatusCode.OK,
    "data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n" +
    "data: [DONE]\n");
var capTokenClient = new ChatClient(new HttpClient(capToken)
{
    BaseAddress = new Uri(AthenaApi.WorkerBase),
});
await capTokenClient.SendMessageAsync("hello", null, "c1", "tok-1", viaVoice: false);
Check("m2: chat with token sends Authorization Bearer",
    capToken.LastRequest?.Headers.TryGetValues("Authorization", out var bearer) == true
    && bearer.First() == "Bearer tok-1");
Check("m2: chat with token sends NO site-key header",
    capToken.LastRequest?.Headers.Contains("X-Vesper-Key") == false);
Check("m2: typed chat body has no via_voice",
    !capToken.LastBody.Contains("via_voice"));

var capVoice = new CaptureHandler(HttpStatusCode.OK,
    "data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n" +
    "data: [DONE]\n");
var capVoiceClient = new ChatClient(new HttpClient(capVoice)
{
    BaseAddress = new Uri(AthenaApi.WorkerBase),
});
await capVoiceClient.SendMessageAsync("wolf", "k", "c1", "tok-1", viaVoice: true);
Check("m2: mic chat body carries via_voice:true",
    capVoice.LastBody.Contains("\"via_voice\":true"));
Check("m2: mic chat still sends Bearer",
    capVoice.LastRequest?.Headers.TryGetValues("Authorization", out var vb) == true
    && vb.First() == "Bearer tok-1");

var capKey = new CaptureHandler(HttpStatusCode.OK,
    "data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n" +
    "data: [DONE]\n");
var capKeyClient = new ChatClient(new HttpClient(capKey)
{
    BaseAddress = new Uri(AthenaApi.WorkerBase),
});
await capKeyClient.SendMessageAsync("hello", "key-1", "c1", null, viaVoice: false);
Check("m2: chat with key only sends X-Vesper-Key (grace)",
    capKey.LastRequest?.Headers.TryGetValues("X-Vesper-Key", out var kb) == true
    && kb.First() == "key-1");
Check("m2: chat with key only sends NO Authorization header",
    capKey.LastRequest?.Headers.Contains("Authorization") == false);

var capAuth = new CaptureHandler(HttpStatusCode.BadRequest, "{}");
var capAuthClient = new ChatClient(new HttpClient(capAuth)
{
    BaseAddress = new Uri(AthenaApi.WorkerBase),
});
Eq("m2: ValidateAuthAsync with token 400 means valid",
    await capAuthClient.ValidateAuthAsync("tok-1", null), KeyValidationResult.Valid);
var noCredsThrew = false;
try { await capAuthClient.SendMessageAsync("hi", null, "c1", null, viaVoice: false); }
catch (ArgumentException) { noCredsThrew = true; }
Check("m2: chat with neither token nor key rejected client-side", noCredsThrew);

// DataClient: Bearer token is used for reads when stored.
var capData = new CaptureHandler(HttpStatusCode.OK,
    "{\"exported_at\":\"2026-11-17T12:00:00.000Z\",\"count\":0,\"messages\":[]}");
var capDataClient = new DataClient(new HttpClient(capData)
{
    BaseAddress = new Uri(AthenaApi.WorkerBase),
});
await capDataClient.GetBackupAsync("key-1", authToken: "tok-1");
Check("m2: backup with token sends Authorization Bearer",
    capData.LastRequest?.Headers.TryGetValues("Authorization", out var db) == true
    && db.First() == "Bearer tok-1");
Check("m2: backup with token sends NO site-key header",
    capData.LastRequest?.Headers.Contains("X-Vesper-Key") == false);

// ---------- DPAPI token storage (Windows-only) ----------
if (OperatingSystem.IsWindows())
{
    SecureStore.SaveKey("m2-test-key-roundtrip");
    SecureStore.SaveToken("m2-test-token-roundtrip");
    Eq("m2 dpapi: token round-trips",
        SecureStore.TryLoadToken(), "m2-test-token-roundtrip");
    Eq("m2 dpapi: saving a token keeps the key",
        SecureStore.TryLoadKey(), "m2-test-key-roundtrip");
    SecureStore.DeleteKey();
    Check("m2 dpapi: DeleteKey removes the key but keeps the token",
        SecureStore.TryLoadKey() is null
        && SecureStore.TryLoadToken() == "m2-test-token-roundtrip");
    SecureStore.DeleteToken();
    Check("m2 dpapi: DeleteToken removes the token (sign out)",
        SecureStore.TryLoadToken() is null);
}
else
{
    Console.WriteLine("SKIP m2 dpapi: Windows-only (run on the laptop to verify).");
}

// ---------- N5 M3: honest offline/error states ----------
// Gap 1: HTTP 500 is plain language — problem + not answered +
// try again — and claims no cause (a 500 can be the daily AI
// quota or a real fault; the app cannot tell which).
var mockChat500 = new ChatClient(MockClient((HttpStatusCode)500));
var chat500Msg = "";
try { await mockChat500.SendMessageAsync("hello", "k", "c1"); }
catch (AthenaApiException ex) { chat500Msg = ex.Message; }
Eq("m3: chat 500 is the plain-language service-problem message",
    chat500Msg, ErrorMessages.ServiceProblemChat);
Check("m3: chat 500 message states problem + not answered + try again",
    ErrorMessages.ServiceProblemChat.Contains("had a problem")
    && ErrorMessages.ServiceProblemChat.Contains("not answered")
    && ErrorMessages.ServiceProblemChat.Contains("try again"));
Check("m3: chat 500 message claims no cause (no quota, no bare status)",
    !ErrorMessages.ServiceProblemChat.Contains("quota", StringComparison.OrdinalIgnoreCase)
    && !ErrorMessages.ServiceProblemChat.Contains("HTTP 500")
    && !ErrorMessages.ServiceProblemChat.Contains("limit", StringComparison.OrdinalIgnoreCase));

var mockData500 = new DataClient(MockClient((HttpStatusCode)500));
async Task<string?> Data500Message(Func<DataClient, Task> call)
{
    try { await call(mockData500); return null; }
    catch (AthenaApiException ex) { return ex.Message; }
}
Eq("m3: device status 500 is the plain-language request message",
    await Data500Message(d => d.GetDeviceStatusAsync("k")),
    ErrorMessages.ServiceProblemRequest);
Eq("m3: device activity 500 is the plain-language request message",
    await Data500Message(d => d.GetDeviceActivityAsync("k")),
    ErrorMessages.ServiceProblemRequest);
Eq("m3: memories 500 is the plain-language request message",
    await Data500Message(d => d.GetMemoriesAsync("k", "c1")),
    ErrorMessages.ServiceProblemRequest);
Eq("m3: backup 500 is the plain-language request message",
    await Data500Message(d => d.GetBackupAsync("k")),
    ErrorMessages.ServiceProblemRequest);
Check("m3: request 500 message states problem + not answered + try again",
    ErrorMessages.ServiceProblemRequest.Contains("had a problem")
    && ErrorMessages.ServiceProblemRequest.Contains("not answered")
    && ErrorMessages.ServiceProblemRequest.Contains("try again"));
Check("m3: request 500 message claims no cause",
    !ErrorMessages.ServiceProblemRequest.Contains("quota", StringComparison.OrdinalIgnoreCase)
    && !ErrorMessages.ServiceProblemRequest.Contains("HTTP 500"));

var mockAuth500 = new AuthClient(MockClient((HttpStatusCode)500));
var auth500Msg = "";
try { await mockAuth500.LoginAsync("some-password"); }
catch (AthenaApiException ex) { auth500Msg = ex.Message; }
Eq("m3: auth 500 is the plain-language request message",
    auth500Msg, ErrorMessages.ServiceProblemRequest);

// Other 5xx stay generic — only 500 gets the plain-language text.
var mockChat503 = new ChatClient(MockClient((HttpStatusCode)503));
var chat503Msg = "";
try { await mockChat503.SendMessageAsync("hello", "k", "c1"); }
catch (AthenaApiException ex) { chat503Msg = ex.Message; }
Eq("m3: chat 503 stays a bare status (only 500 is reworded)",
    chat503Msg, "The service returned HTTP 503.");

// Gap 4 (core side): the approved-prompt guard rejects anything
// not on the list — null, wrong case, trailing space.
Check("m3: null prompt is not approved",
    !DeviceQuickActions.IsApprovedPrompt(null!));
Check("m3: prompt matching is case-sensitive",
    !DeviceQuickActions.IsApprovedPrompt("ping google.com"));
Check("m3: prompt with trailing space is not approved",
    !DeviceQuickActions.IsApprovedPrompt("Ping google.com "));

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
    // N2 live: model-info tools match the static catalogue exactly.
    var liveData = new DataClient();
    var liveInfo = await liveData.GetModelInfoAsync();
    Check("live: model-info tools match ToolCatalog",
        liveInfo.Tools.OrderBy(t => t).SequenceEqual(
            ToolCatalog.All.Select(t => t.Name).OrderBy(t => t)),
        $"server=[{string.Join(",", liveInfo.Tools)}]");
    // N2 live: backup reads and groups (read-only, no rows created).
    var liveBackup = await liveData.GetBackupAsync(liveKey);
    var liveGroups = ConversationStore.GroupMessages(liveBackup.Messages);
    Check("live: backup groups into conversations",
        liveGroups.Count >= 1 && liveBackup.Count == liveBackup.Messages.Length);
}
else
{
    Console.WriteLine("SKIP live: set ATHENA_TEST_KEY to run backend tests.");
}

// ---------- Startup resources (regression: N1 icon crash) ----------
// The first two builds crashed on launch: (1) icon.ico was not a WPF
// resource at all, (2) the ICO PIL wrote was PNG-compressed throughout
// and WPF's decoder rejects it ("The image decoder cannot decode the
// image"). These byte-level checks run anywhere (no WPF runtime needed)
// and fail on either broken form.
string? appDir = new[]
{
    Environment.GetEnvironmentVariable("ATHENA_APP_DIR"),
    "athena-app/src/AthenaApp",
    "src/AthenaApp",
    "../src/AthenaApp",
    "../../src/AthenaApp",
}.FirstOrDefault(d => d is not null && Directory.Exists(d));
if (appDir is null)
{
    failures.Add("icon-resources: AthenaApp dir not found");
    Console.WriteLine("FAIL icon-resources: AthenaApp dir not found (searched ATHENA_APP_DIR, athena-app/src/AthenaApp, src/AthenaApp, ../src/AthenaApp, ../../src/AthenaApp)");
}
else
{
    // ICO: valid ICONDIR, every entry BMP/DIB (BITMAPINFOHEADER = 40),
    // never PNG — WPF's decoder cannot decode PNG-compressed ICOs.
    var ico = File.ReadAllBytes(Path.Combine(appDir, "icon.ico"));
    var icoOk = ico.Length >= 6
        && BitConverter.ToUInt16(ico, 0) == 0
        && BitConverter.ToUInt16(ico, 2) == 1
        && BitConverter.ToUInt16(ico, 4) >= 1;
    var count = icoOk ? BitConverter.ToUInt16(ico, 4) : 0;
    var allBmp = icoOk;
    for (var i = 0; i < count && allBmp; i++)
    {
        var off = 6 + i * 16;
        if (off + 16 > ico.Length) { allBmp = false; break; }
        var imgOff = BitConverter.ToInt32(ico, off + 12);
        if (imgOff + 4 > ico.Length) { allBmp = false; break; }
        // PNG magic, or a header size other than 40, is not classic BMP.
        if (ico[imgOff] == 0x89 || BitConverter.ToInt32(ico, imgOff) != 40)
            allBmp = false;
    }
    Check("icon: ICO is classic BMP/DIB (no PNG entries)", allBmp);

    // PNG: valid signature + 256x256 IHDR (width is big-endian).
    var png = File.ReadAllBytes(Path.Combine(appDir, "icon.png"));
    var pngOk = png.Length > 24
        && png[0] == 0x89 && png[1] == (byte)'P' && png[2] == (byte)'N'
        && png[3] == (byte)'G'
        && (png[16] << 24 | png[17] << 16 | png[18] << 8 | png[19]) == 256
        && (png[20] << 24 | png[21] << 16 | png[22] << 8 | png[23]) == 256;
    Check("icon: PNG is valid 256x256", pngOk);

    // The window's Icon attribute must name a file that exists and is
    // listed as a WPF Resource — otherwise the window fails to load.
    var xaml = File.ReadAllText(Path.Combine(appDir, "MainWindow.xaml"));
    var m = System.Text.RegularExpressions.Regex.Match(xaml, "Icon=\"([^\"]+)\"");
    Check("icon: MainWindow has an Icon", m.Success);
    if (m.Success)
    {
        var iconFile = m.Groups[1].Value;
        Check($"icon: referenced file exists ({iconFile})",
            File.Exists(Path.Combine(appDir, iconFile)));
        var csproj = File.ReadAllText(Path.Combine(appDir, "AthenaApp.csproj"));
        Check($"icon: referenced file is a WPF Resource ({iconFile})",
            csproj.Contains($"<Resource Include=\"{iconFile}\""));
    }

    // WPF needs real globalization data at window load (it resolves the
    // system UI culture, e.g. 1033 en-US). Invariant mode crashes the
    // app on launch with "Only the invariant culture is supported in
    // globalization-invariant mode" — the third N1 launch failure.
    var csprojText = File.ReadAllText(Path.Combine(appDir, "AthenaApp.csproj"));
    Check("wpf: InvariantGlobalization is NOT enabled",
        !csprojText.Contains("<InvariantGlobalization>true</InvariantGlobalization>"));
    Check("wpf: UseWPF is enabled",
        csprojText.Contains("<UseWPF>true</UseWPF>"));

    // Redesign (minimal black identity): no dashboard, no sidebar,
    // no Island. The six sections live behind the "···" menu overlay.
    var shell = File.ReadAllText(Path.Combine(appDir, "MainWindow.xaml"));
    Check("shell: black background", shell.Contains("Background=\"#000000\""));
    Check("shell: wordmark present", shell.Contains("x:Name=\"WordmarkText\""));
    Check("shell: blue glow present", shell.Contains("x:Name=\"BlueGlow\""));
    Check("shell: red glow present", shell.Contains("x:Name=\"RedGlow\""));
    Check("shell: menu button present", shell.Contains("x:Name=\"MenuButton\""));
    Check("shell: overlay present", shell.Contains("x:Name=\"Overlay\""));
    Check("shell: chat area present", shell.Contains("x:Name=\"ChatArea\""));
    Check("shell: no old content area", !shell.Contains("x:Name=\"ContentArea\""));
    Check("shell: no old nav panel", !shell.Contains("x:Name=\"NavPanel\""));
    foreach (var view in new[]
        { "ChatView", "ConversationsView", "MemoryVaultView",
          "ToolsView", "DevicesView", "ActivityView", "SettingsView" })
        Check($"shell: view exists ({view})",
            File.Exists(Path.Combine(appDir, "Views", view + ".xaml")) &&
            File.Exists(Path.Combine(appDir, "Views", view + ".xaml.cs")));
    Check("redesign: DashboardView deleted",
        !File.Exists(Path.Combine(appDir, "Views", "DashboardView.xaml")) &&
        !File.Exists(Path.Combine(appDir, "Views", "DashboardView.xaml.cs")));
    Check("redesign: IslandWindow deleted",
        !File.Exists(Path.Combine(appDir, "IslandWindow.xaml")) &&
        !File.Exists(Path.Combine(appDir, "IslandWindow.xaml.cs")));

    // Presence wiring (structural: the window itself needs Windows).
    var chatViewCs = File.ReadAllText(Path.Combine(appDir, "Views", "ChatView.xaml.cs"));
    Check("presence: ChatView reports Thinking",
        chatViewCs.Contains("SetPresenceState(PresenceState.Thinking"));
    Check("presence: ChatView reports Idle on reply",
        chatViewCs.Contains("SetPresenceState(PresenceState.Idle"));
    Check("presence: ChatView reports Offline on failure",
        chatViewCs.Contains("SetPresenceState(PresenceState.Offline"));
    Check("presence: ChatView raises MessagesChanged",
        chatViewCs.Contains("MessagesChanged"));
    var mainCs = File.ReadAllText(Path.Combine(appDir, "MainWindow.xaml.cs"));
    Check("redesign: MainWindow has no Island window",
        !mainCs.Contains("IslandWindow") && !mainCs.Contains("SetIslandState"));
    Check("redesign: MainWindow deletes the leftover island file",
        mainCs.Contains("island.json"));
    foreach (var section in new[]
        { "Conversations", "Memory Vault", "Tools", "Devices", "Activity", "Settings" })
        Check($"menu: section offered ({section})",
            mainCs.Contains($"\"{section}\""));
    var settingsCs = File.ReadAllText(Path.Combine(appDir, "Views", "SettingsView.xaml.cs"));
    Check("redesign: Settings has no Island card",
        !settingsCs.Contains("RequestShowIsland") && !settingsCs.Contains("Island"));

    // Arc reactor identity + transparent chat (structural: the
    // window itself needs Windows).
    Check("reactor: Reactor present",
        shell.Contains("x:Name=\"Reactor\""));
    Check("reactor: ReactorGlow present",
        shell.Contains("x:Name=\"ReactorGlow\""));
    Check("reactor: ReactorCore present",
        shell.Contains("x:Name=\"ReactorCore\""));
    Check("reactor: MainWindow flashes on reply",
        mainCs.Contains("FlashReactor") && mainCs.Contains("ReplyReceived"));
    Check("reactor: MainWindow sizes the reactor",
        mainCs.Contains("AnimateReactorSize(260)") && mainCs.Contains("AnimateReactorSize(84)"));
    Check("chat: bubbles are glass",
        chatViewCs.Contains("FindResource(\"BrushGlassFill\")"));
    Check("chat: scroll waits for layout",
        chatViewCs.Contains("Dispatcher.BeginInvoke") && chatViewCs.Contains("ScrollToEnd"));
    Check("chat: ReplyReceived event raised",
        chatViewCs.Contains("ReplyReceived?.Invoke()"));

    // Frosted glass (structural).
    var appXaml = File.ReadAllText(Path.Combine(appDir, "App.xaml"));
    Check("glass: glass brushes exist",
        appXaml.Contains("x:Key=\"BrushGlassFill\"") && appXaml.Contains("x:Key=\"BrushGlassBorder\""));
    Check("glass: custom title bar present",
        shell.Contains("MinimizeButton_Click") && shell.Contains("MaximizeButton_Click")
        && shell.Contains("CloseButton_Click"));
    Check("glass: caption buttons hit-test visible in chrome",
        shell.Contains("IsHitTestVisibleInChrome"));
    Check("glass: title bar uses WindowChrome",
        shell.Contains("WindowChrome"));
    var chatXaml = File.ReadAllText(Path.Combine(appDir, "Views", "ChatView.xaml"));
    Check("glass: input bar is glass",
        chatXaml.Contains("BrushGlassFill"));

    // N5 M1: Companion integration (structural: the window itself needs Windows).
    var devicesCs = File.ReadAllText(Path.Combine(appDir, "Views", "DevicesView.xaml.cs"));
    Check("m1: DevicesView offers quick actions",
        devicesCs.Contains("DeviceQuickActions.All"));
    Check("m1: DevicesView has an Unpair action",
        devicesCs.Contains("Unpair this laptop"));
    Check("m1: DevicesView explains the revoke limit honestly",
        devicesCs.Contains("the site key cannot revoke"));
    Check("m1: ChatView sends device commands",
        chatViewCs.Contains("SendDeviceCommandAsync"));
    Check("m1: device turns show Working, not Thinking",
        chatViewCs.Contains("PresenceState.Working, PresenceSource.Device"));
    Check("m1: approval-waiting replies show Approval",
        chatViewCs.Contains("PresenceState.Approval, PresenceSource.Device"));
    Check("m1: MainWindow wires the Devices quick actions",
        mainCs.Contains("SendDeviceCommandAsync"));
    var activityXaml = File.ReadAllText(Path.Combine(appDir, "Views", "ActivityView.xaml"));
    Check("m1: ActivityView has a Refresh button",
        activityXaml.Contains("x:Name=\"RefreshButton\""));

    // N5 M2: password login + token migration + via_voice (structural:
    // the window itself needs Windows).
    var authCs = File.ReadAllText(Path.Combine(appDir, "..", "Athena.Core", "AuthClient.cs"));
    Check("m2: AuthClient exists with the auth endpoints",
        authCs.Contains("/api/auth/login") && authCs.Contains("/api/auth/setup")
        && authCs.Contains("/api/auth/exchange") && authCs.Contains("/api/auth/status"));
    Check("m2: AuthClient sends Bearer tokens",
        authCs.Contains("\"Bearer \""));
    Check("m2: via_voice is built only for mic messages",
        authCs.Contains("via_voice"));
    Check("m2: password minimum is 8",
        authCs.Contains("MinPasswordLength = 8"));
    var setupXaml = File.ReadAllText(Path.Combine(appDir, "SetupWindow.xaml"));
    Check("m2: SetupWindow is a password screen, not a key paste",
        setupXaml.Contains("x:Name=\"PasswordBox\"")
        && setupXaml.Contains("x:Name=\"ConfirmBox\"")
        && setupXaml.Contains("Sign in"));
    var setupCs = File.ReadAllText(Path.Combine(appDir, "SetupWindow.xaml.cs"));
    Check("m2: SetupWindow logs in via /api/auth/login",
        setupCs.Contains("LoginAsync"));
    Check("m2: SetupWindow sets up via /api/auth/setup",
        setupCs.Contains("SetupAsync"));
    Check("m2: SetupWindow checks /api/auth/status for its mode",
        setupCs.Contains("GetStatusAsync"));
    Check("m2: SetupWindow stores the token, never the password",
        setupCs.Contains("SaveToken") && !setupCs.Contains("SavePassword"));
    Check("m2: SetupWindow keeps a legacy key fallback for pre-deploy",
        setupCs.Contains("LegacyKey"));
    Check("m2: MainWindow migrates a stored key to a token on startup",
        mainCs.Contains("ExchangeAsync") && mainCs.Contains("DeleteKey")
        && mainCs.Contains("TryLoadToken"));
    Check("m2: MainWindow validates with the token first",
        mainCs.Contains("ValidateAuthAsync"));
    Check("m2: ChatView tracks mic origin in a flag",
        chatViewCs.Contains("_micFilled"));
    Check("m2: ChatView clears the mic flag on hand edits",
        chatViewCs.Contains("InputBox_TextChanged"));
    Check("m2: ChatView passes via_voice to the chat client",
        chatViewCs.Contains("viaVoice"));
    Check("m2: ChatView never tags device commands as voice",
        chatViewCs.Contains("viaVoice = _micFilled && !deviceCommand"));
    Check("m2: DevicesView hints the wolf word, with no button for it",
        devicesCs.Contains("wolf") && devicesCs.Contains("no button for it"));
    Check("m2: DevicesView has no tap-to-revoke button",
        !devicesCs.Contains("RevokeButton") && !devicesCs.Contains("revokeButton"));

    // N4 voice wiring (structural: SAPI itself needs Windows).
    Check("voice: System.Speech referenced",
        csprojText.Contains("<PackageReference Include=\"System.Speech\""));
    Check("voice: VoiceService exists",
        File.Exists(Path.Combine(appDir, "Services", "VoiceService.cs")));
    var chatViewXaml = File.ReadAllText(Path.Combine(appDir, "Views", "ChatView.xaml"));
    Check("voice: ChatView has a Mic button",
        chatViewXaml.Contains("x:Name=\"MicButton\""));
    Check("voice: ChatView has a voice toggle",
        chatViewXaml.Contains("x:Name=\"VoiceToggleButton\""));
    Check("voice: ChatView has a listening indicator",
        chatViewXaml.Contains("x:Name=\"ListeningText\""));
    Check("voice: ChatView speaks replies when enabled",
        chatViewCs.Contains("Voice?.Speak(reply)"));
    Check("voice: ChatView interrupts speech on send",
        chatViewCs.Contains("Voice?.StopSpeaking()"));
    Check("voice: recognised text goes to the input box, never auto-sent",
        chatViewCs.Contains("InputBox.Text = text"));
    Check("voice: Settings has a real Voice section",
        settingsCs.Contains("VoiceSelection.Pick"));
    Check("voice: Settings persists voice preferences",
        settingsCs.Contains("SaveVoiceSettings()"));
    Check("voice: MainWindow creates the voice service",
        mainCs.Contains("new VoiceService()"));
    Check("voice: MainWindow disposes voice on close",
        mainCs.Contains("Voice?.Dispose()"));

    // N5 M3: honest offline/error states (structural: the views
    // themselves need Windows).
    var chatClientCs = File.ReadAllText(Path.Combine(appDir, "..", "Athena.Core", "ChatClient.cs"));
    var dataClientCs = File.ReadAllText(Path.Combine(appDir, "..", "Athena.Core", "DataClient.cs"));
    var activityCs = File.ReadAllText(Path.Combine(appDir, "Views", "ActivityView.xaml.cs"));
    var memoryCs = File.ReadAllText(Path.Combine(appDir, "Views", "MemoryVaultView.xaml.cs"));
    Check("m3: chat maps 500 to the plain-language message",
        chatClientCs.Contains("500 => ErrorMessages.ServiceProblemChat"));
    Check("m3: device reads map 500 to the plain-language message",
        dataClientCs.Contains("500 => ErrorMessages.ServiceProblemRequest"));
    Check("m3: auth maps 500 to the plain-language message",
        authCs.Contains("500 => ErrorMessages.ServiceProblemRequest"));
    Check("m3: no core client quotes a bare HTTP 500 anywhere",
        !chatClientCs.Contains("HTTP 500") && !dataClientCs.Contains("HTTP 500")
        && !authCs.Contains("HTTP 500"));
    Check("m3: FriendlyError passes the service message through unchanged",
        File.ReadAllText(Path.Combine(appDir, "Views", "ViewHelpers.cs"))
            .Contains("AthenaApiException api => api.Message"));
    Check("m3: Devices refresh failure shows what to do next",
        devicesCs.Contains("RetryHintRefresh"));
    Check("m3: Activity refresh failure shows what to do next",
        activityCs.Contains("RetryHintRefresh"));
    Check("m3: Memory Vault refresh failure shows what to do next",
        memoryCs.Contains("RetryHintReopen"));
    Check("m3: Memory Vault clears cached rows on a failed refresh",
        memoryCs.Contains("_all = new();"));
    Check("m3: Memory Vault clears the detail pane on a failed refresh",
        memoryCs.Contains("ShowNoMemorySelected()"));
    Check("m3: quick actions re-check status after a turn",
        devicesCs.Split("GetDeviceStatusAsync").Length - 1 >= 2);
    Check("m3: quick actions never re-enable from the captured status",
        !devicesCs.Contains("button.IsEnabled = status.Online"));
    Check("m3: quick actions take the fresh status after a turn",
        devicesCs.Contains("b.IsEnabled = online && _sendDeviceCommand is not null"));
    Check("m3: a failed status re-check leaves the buttons disabled",
        devicesCs.Contains("b.IsEnabled = false"));
    // Gap 4: the refused-prompt path in SendDeviceCommandAsync
    // must return before any send — and add no bubble. Bubbles
    // are only added inside SendAsync via AddMessage, so a
    // refused prompt that never reaches SendAsync can never
    // show a fake success.
    var gateStart = chatViewCs.IndexOf("SendDeviceCommandAsync", StringComparison.Ordinal);
    var gateBody = gateStart >= 0
        ? chatViewCs[gateStart..chatViewCs.IndexOf(
            "private async Task SendAsync", gateStart, StringComparison.Ordinal)]
        : "";
    Check("m3: refused device prompt returns before sending",
        gateBody.Contains("IsApprovedPrompt")
        && gateBody.Contains("return Task.CompletedTask;"));
    Check("m3: the guard runs before the send",
        gateBody.IndexOf("IsApprovedPrompt", StringComparison.Ordinal)
        < gateBody.IndexOf("SendAsync", StringComparison.Ordinal));
    Check("m3: refused device prompt adds no bubble",
        !gateBody.Contains("AddMessage"));
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

/// <summary>Records the last request (headers + body) so M2 tests can prove which auth header was sent and whether via_voice was tagged.</summary>
sealed class CaptureHandler : HttpMessageHandler
{
    public HttpRequestMessage? LastRequest;
    public string LastBody = "";
    private readonly HttpStatusCode _status;
    private readonly string _body;
    public CaptureHandler(HttpStatusCode status, string body)
    {
        _status = status;
        _body = body;
    }
    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken ct)
    {
        LastRequest = request;
        if (request.Content is not null)
            LastBody = await request.Content.ReadAsStringAsync(ct);
        return new HttpResponseMessage(_status)
        {
            Content = new StringContent(_body, Encoding.UTF8, "application/json"),
        };
    }
}
