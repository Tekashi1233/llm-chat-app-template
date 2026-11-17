namespace Athena.Core;

/// <summary>
/// Plain-language error text shared by the API clients, so a
/// failure says what happened and what to do next instead of
/// quoting a bare HTTP status.
///
/// HTTP 500 deliberately claims NO cause: a 500 can be the daily
/// AI quota being used up or a real fault on the service, and the
/// app cannot tell which — so the message says only that the
/// service had a problem, the request was not answered, and to
/// try again. Never blame the quota, the network, or the laptop
/// for a 500; none of those is known from the status alone.
/// </summary>
public static class ErrorMessages
{
    /// <summary>HTTP 500 on a chat turn: the message was not answered.</summary>
    public const string ServiceProblemChat =
        "The service had a problem and your message was not answered — try again.";

    /// <summary>
    /// HTTP 500 on a read (device status, activity, memories,
    /// backup, auth): the request was not answered.
    /// </summary>
    public const string ServiceProblemRequest =
        "The service had a problem and the request was not answered — try again.";
}
