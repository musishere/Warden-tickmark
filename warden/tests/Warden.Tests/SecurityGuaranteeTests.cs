/*
 * SecurityGuaranteeTests.cs — the security promises Warden makes, one test each.
 *
 * Each test name is a guarantee in plain English. They are all skipped for now
 * ([Fact(Skip = ...)]) and get switched on as the matching step in MVP.md lands.
 * If one of these ever fails, Warden has broken a promise.
 */
using Xunit;

namespace Warden.Tests;

// Tests are named after security guarantees. Implement as features land.
public class SecurityGuaranteeTests
{
    [Fact(Skip = "week 2")] public void agent_cannot_exceed_user_permissions() { }
    [Fact(Skip = "week 2")] public void ethical_wall_blocks_even_admin() { }
    [Fact(Skip = "week 5")] public void scim_deactivation_revokes_agent_tokens() { }
    [Fact(Skip = "week 2")] public void expired_delegated_token_is_rejected() { }
    [Fact(Skip = "week 5")] public void concurrent_requests_cannot_overspend_credits() { }
    [Fact(Skip = "week 4")] public void prompt_injection_cannot_trigger_export() { }
    [Fact(Skip = "week 5")] public void override_requires_human_approval() { }
    [Fact(Skip = "week 2")] public void tenant_a_cannot_read_tenant_b() { }
}
