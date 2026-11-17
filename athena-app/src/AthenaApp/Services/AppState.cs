using Athena.Core;

namespace Athena;

/// <summary>
/// Session state shared by the MainWindow shell and all views:
/// the backend clients, the site key, the current conversation,
/// and the last loaded model info / backup snapshot.
/// </summary>
public sealed class AppState
{
    public ChatClient Chat { get; } = new();
    public DataClient Data { get; } = new();
    public string? ApiKey { get; set; }
    public string ConversationId { get; set; } = "";
    public ModelInfo? ModelInfo { get; set; }
    public BackupExport? Backup { get; set; }

    public event Action? ConversationChanged;

    public void OpenConversation(string id)
    {
        ConversationId = id;
        ConversationChanged?.Invoke();
    }

    public void NewConversation() => OpenConversation(Guid.NewGuid().ToString());
}
