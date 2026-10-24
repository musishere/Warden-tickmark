/*
 * Agent.cs — a registered AI agent (e.g. Tickmark, the invoice-vouching agent).
 *
 * An agent acts only for a specific user, with a short-lived pass (step 6). Agents are not
 * tied to one tenant: the same agent can work for Firm A and Firm B.
 *
 * AllowedScopes is the agent's ceiling (step 18): the most it may ever be given, whoever it
 * works for. Space-separated, like a pass's scope ("documents.read vouch.run"). A pass is
 * what was asked for, and only if it's within BOTH the user's role AND this ceiling.
 * Empty = nothing (fail closed).
 *
 * EntraClientId is how Warden KNOWS which agent is calling (step 17a): each agent is an app
 * registration in Entra, and every sign-in token Entra issues to it carries that app's client id
 * (`azp`). Warden trusts that signed claim, never an id the agent sends itself. No agent with that
 * client id -> refused, until the firm's admin runs `register-agent`.
 */
namespace Warden.Core.Domain;

public class Agent
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public required string Name { get; set; }
    public string AllowedScopes { get; set; } = "";
    public Guid? EntraClientId { get; set; }
}
