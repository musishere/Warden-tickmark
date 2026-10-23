/*
 * DecisionTests.cs — step 7: the decision function for tool calls (Decision.DecideTool).
 *
 *   Sara's pass has documents.read  -> read_document allowed
 *   same pass                       -> run_vouch denied: the pass doesn't carry vouch.run
 *                                      (Sara herself may vouch, she just didn't give the agent that)
 *   Omar (Admin, may export)        -> export_documents denied: agents may never export
 *   agent's registration narrowed   -> denied at once, even for a pass stamped before
 *   a pass for Pepsi held by Sara   -> denied by the wall, even though the pass "says" Pepsi
 *   a made-up tool                  -> denied
 * (Walls beating Admin and firm isolation are in SecurityGuaranteeTests.)
 *
 * The passes here are logbook rows built in memory; the decision never re-reads the logbook
 * (AgentPass.Check did that already), so nothing needs saving.
 */
using Warden.Api.Auth;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class DecisionTests(WardenFactory warden)
{
    Decision.Verdict Tool(Core.Domain.DelegatedToken pass, string tool) =>
        warden.WithDb(db => Decision.DecideTool(db, pass, tool));

    static Core.Domain.DelegatedToken SaraReadsCoke() => WardenFactory.SaraOnCoke(DateTime.UtcNow.AddMinutes(15));

    [Fact]
    public void pass_with_the_right_scope_may_call_the_tool() =>
        Assert.Equal(new Decision.Verdict(true, "allowed"), Tool(SaraReadsCoke(), "read_document"));

    [Fact]
    public void pass_without_the_scope_is_denied() =>
        Assert.Equal(new Decision.Verdict(false, "pass does not include vouch.run"), Tool(SaraReadsCoke(), "run_vouch"));

    [Fact]
    public void agents_may_never_export_even_for_an_admin()
    {
        var pass = SaraReadsCoke();
        pass.UserId = DemoSeed.Omar;  // Admin on Coke: his role allows documents.export
        pass.AgentId = warden.NewAgent("documents.export");  // a careless registration that allows it too
        pass.Scope = "documents.export";
        Assert.Equal(new Decision.Verdict(false, "export_documents may not be called by an agent"), Tool(pass, "export_documents"));
    }

    // Narrowing an agent's registration takes effect on passes it ALREADY holds.
    [Fact]
    public void agent_registration_is_checked_on_every_call()
    {
        var pass = SaraReadsCoke();
        pass.AgentId = warden.NewAgent("documents.read");
        Assert.True(Tool(pass, "read_document").Allowed);

        warden.WithDb(db => { db.Agents.Find(pass.AgentId)!.AllowedScopes = ""; return db.SaveChanges(); });
        Assert.Equal(new Decision.Verdict(false, "Overreach is not registered for: documents.read"), Tool(pass, "read_document"));
    }

    [Fact]
    public void wall_applies_even_if_the_pass_names_the_engagement()
    {
        var pass = SaraReadsCoke();
        pass.EngagementId = DemoSeed.Pepsi;
        Assert.StartsWith("ethical wall", Tool(pass, "read_document").Reason);
    }

    [Fact]
    public void unknown_tool_is_denied() =>
        Assert.Equal(new Decision.Verdict(false, "unknown tool: delete_everything"), Tool(SaraReadsCoke(), "delete_everything"));
}
