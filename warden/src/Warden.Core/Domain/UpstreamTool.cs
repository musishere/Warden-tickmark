/*
 * UpstreamTool.cs — the admin's "yes" for one tool on an upstream MCP server (step 18a).
 *
 * "Firm A's SharePoint tool `search` needs documents.read." Without this row the tool is hidden
 * from tools/list and refused on tools/call (fail closed): a tool a server adds tomorrow stays
 * off until an admin assigns it a scope.
 *
 * Keyed by (TenantId, Name): agents call tools by name, so a name means one tool per firm.
 * ServerId says which server it lives on; the tool is only listed if THAT server offers it.
 */
namespace Warden.Core.Domain;

public class UpstreamTool
{
    public Guid TenantId { get; set; }
    public required string Name { get; set; }
    public Guid ServerId { get; set; }
    public required string Scope { get; set; }
}
