/*
 * Agent.cs — a registered AI agent (e.g. Tickmark, the invoice-vouching agent).
 *
 * An agent has no permissions of its own. It only gets a short-lived pass to act
 * for a specific user (built in step 6). Agents are not tied to one tenant:
 * the same Tickmark can work for Firm A and Firm B.
 */
namespace Warden.Core.Domain;

public class Agent
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public required string Name { get; set; }
}
