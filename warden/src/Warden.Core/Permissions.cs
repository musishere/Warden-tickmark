/*
 * Permissions.cs — which actions ("scopes") each role may do on an engagement.
 *
 *   Staff            documents.read, vouch.run
 *   Manager, Admin   documents.read, vouch.run, documents.export
 *
 * An agent's pass can only carry scopes its user's role has: if Sara is Staff,
 * a pass with documents.export is refused ("an agent can't exceed its user").
 *
 * Scopes travel as one space-separated string, the OAuth convention:
 *   "documents.read vouch.run"
 *
 * Plain C# with no database or web code, so it's trivial to test and reuse
 * (the decision function in step 7 uses it too).
 */
using Warden.Core.Domain;

namespace Warden.Core;

public static class Permissions
{
    public const string DocumentsRead = "documents.read";
    public const string VouchRun = "vouch.run";
    public const string DocumentsExport = "documents.export";

    public static readonly IReadOnlySet<string> AllScopes = new HashSet<string> { DocumentsRead, VouchRun, DocumentsExport };

    // A "switch expression": like a Python match/case that returns a value.
    public static IReadOnlySet<string> ScopesFor(Role role) => role switch
    {
        Role.Staff => new HashSet<string> { DocumentsRead, VouchRun },
        Role.Manager or Role.Admin => new HashSet<string> { DocumentsRead, VouchRun, DocumentsExport },
        _ => new HashSet<string>(),  // unknown role: nothing (fail closed)
    };

    // "documents.read  vouch.run" -> { "documents.read", "vouch.run" }
    public static HashSet<string> Parse(string? scope) =>
        (scope ?? "").Split(' ', StringSplitOptions.RemoveEmptyEntries).ToHashSet();
}
