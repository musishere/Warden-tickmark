/*
 * UpstreamServer.cs — one of a firm's own MCP servers, behind Warden (step 18a).
 *
 * e.g. Firm A's SharePoint MCP server. The firm's admin connects it once; from then on agents
 * reach its tools only through Warden, and never see Url or how Warden signs in to it.
 *
 *   Url       the server's MCP address (Streamable HTTP), e.g. https://sharepoint-mcp.firma.example/mcp
 *   Audience  the "aud" Warden puts on the one-minute token it sends there: the server checks
 *             that the token was signed by Warden (public key at /.well-known/jwks.json) and meant for it.
 *
 * Which tools agents may use, and with which scope, is UpstreamTool. A server's tool with no
 * UpstreamTool row is hidden and refused.
 */
namespace Warden.Core.Domain;

public class UpstreamServer
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid TenantId { get; set; }
    public required string Name { get; set; }
    public required string Url { get; set; }
    public required string Audience { get; set; }
}
