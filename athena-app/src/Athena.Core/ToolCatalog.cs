namespace Athena.Core;

/// <summary>
/// The 13 registered tools, with a one-line user-facing description
/// each. The wording is shortened from the Worker's own
/// TOOL_DEFINITIONS (src/tools.ts) — the authoritative list. The
/// catalogue is static documentation: whether a tool is actually
/// registered right now comes from /api/model-info, checked by
/// <see cref="IsRegistered"/>.
/// </summary>
public sealed record ToolInfo(string Name, string Description, string Category)
{
    public bool IsRegistered(ModelInfo? modelInfo) =>
        modelInfo?.Tools.Contains(Name) ?? false;
}

public static class ToolCatalog
{
    public static readonly ToolInfo[] All =
    {
        new("get_weather",
            "Current weather for any city or place in the world.",
            "Knowledge"),
        new("search_web",
            "Look up a topic, person, place or fact on Wikipedia, with a short summary and a link.",
            "Knowledge"),
        new("calculate",
            "Evaluate a maths expression exactly, instead of working it out by hand.",
            "Knowledge"),
        new("get_datetime",
            "Current date and time, in any timezone you ask for.",
            "Knowledge"),
        new("get_device_status",
            "Check whether the paired Windows laptop is on, and how it is doing: CPU, memory, disks and network.",
            "Laptop"),
        new("open_app",
            "Open an approved app on the paired laptop, e.g. Notepad or Chrome.",
            "Laptop"),
        new("search_files",
            "Find files by name in an approved folder on the paired laptop.",
            "Laptop"),
        new("create_file",
            "Create a new text file in an approved folder on the paired laptop. Asks for approval on screen first.",
            "Laptop"),
        new("edit_file",
            "Add to or replace text in a file on the paired laptop. Asks for approval on screen first.",
            "Laptop"),
        new("read_file",
            "Read a text file in an approved folder on the paired laptop.",
            "Laptop"),
        new("run_command",
            "Run an approved command on the paired laptop, e.g. ping, and get its output back.",
            "Laptop"),
        new("get_action_history",
            "What ATHENA has done on the paired laptop lately: apps opened, files changed, commands run, refusals.",
            "Laptop"),
        new("remember_fact",
            "Remember something durable about you for later conversations: a fact, a preference, a project fact or a plan.",
            "Memory"),
    };
}
