/*
 * AdminTests.cs — step 22: the firm admin's endpoints, against the FAKE Entra (WardenFactory).
 *
 *   Warden.Admin role, signed in at the admin page (azp = Warden's own app) -> in, to their own firm
 *   the same admin's AGENT (azp = Tickmark), same role in the token          -> 403
 *   signed in at the admin page, no Warden.Admin role                        -> 403
 *   a dev token                                                              -> 401
 *
 * And what an admin does (22b): add a person with their Microsoft account, put them on an engagement,
 * take them off, each change logged; another firm's people and engagements are "not found".
 * 22c: an admin's wall beats even the Admin role, and lifting it lets them back in.
 * 22d: an agent the admin allows works for THIS firm only, up to the ceiling the admin set.
 * 22e: a connected server's tool is hidden until the admin assigns it, and hidden again on unassign.
 * 22f: the firm's audit log, newest first, paged, never another firm's rows.
 * 22g: the page itself is public (it holds no data); it learns where to sign in from /admin/sign-in.
 */
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Warden.Api.Data;
using Warden.Core.Domain;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class AdminTests(WardenFactory warden)
{
    static readonly string[] Admin = ["Warden.Admin"];

    HttpClient With(string token)
    {
        var client = warden.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client;
    }

    [Fact]
    public async Task only_a_warden_admin_signed_in_at_the_admin_page_gets_in()
    {
        var (_, oid) = warden.NewLinkedCokeStaff();  // a Firm A person, linked to an Entra account

        var res = await With(WardenFactory.EntraToken(oid, azp: WardenFactory.FakeEntraClient, roles: Admin)).GetAsync("/admin/firm");
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
        Assert.Equal("Firm A", (await res.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("name").GetString());

        Assert.Equal(HttpStatusCode.Forbidden,
            (await With(WardenFactory.EntraToken(oid, roles: Admin)).GetAsync("/admin/firm")).StatusCode);  // their agent
        Assert.Equal(HttpStatusCode.Forbidden,
            (await With(WardenFactory.EntraToken(oid, azp: WardenFactory.FakeEntraClient)).GetAsync("/admin/firm")).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized,
            (await warden.ClientFor(DemoSeed.Sara, DemoSeed.FirmA).GetAsync("/admin/firm")).StatusCode);
    }

    [Fact]
    public async Task only_a_firm_admin_can_change_its_own_firm()
    {
        var (_, adminOid) = warden.NewLinkedCokeStaff();
        var admin = With(WardenFactory.EntraToken(adminOid, azp: WardenFactory.FakeEntraClient, roles: Admin));

        // Add a person with their Microsoft account: they can sign in, but aren't on Coke yet.
        var oid = Guid.NewGuid();
        var email = $"{Guid.NewGuid()}@firma.test";
        var added = await admin.PostAsJsonAsync("/admin/people", new { name = "Newcomer", email, entraObjectId = oid });
        Assert.Equal(HttpStatusCode.Created, added.StatusCode);
        var id = (await added.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetGuid();
        var person = With(WardenFactory.EntraToken(oid));
        Assert.Equal(HttpStatusCode.Forbidden, (await person.GetAsync($"/audit?engagement={DemoSeed.Coke}")).StatusCode);

        // The same email or Microsoft account twice, a made-up role: refused.
        Assert.Equal(HttpStatusCode.Conflict, (await admin.PostAsJsonAsync("/admin/people", new { name = "Again", email })).StatusCode);
        Assert.Equal(HttpStatusCode.Conflict, (await admin.PutAsJsonAsync($"/admin/people/{DemoSeed.Ali}/entra", new { objectId = oid })).StatusCode);
        Assert.Equal(HttpStatusCode.BadRequest, (await admin.PutAsJsonAsync($"/admin/engagements/{DemoSeed.Coke}/members/{id}", new { role = "1" })).StatusCode);

        // On Coke as Staff: now they're in, and the trail shows who put them there.
        Assert.Equal(HttpStatusCode.OK, (await admin.PutAsJsonAsync($"/admin/engagements/{DemoSeed.Coke}/members/{id}", new { role = "Staff" })).StatusCode);
        var trail = await person.GetFromJsonAsync<JsonElement>($"/audit?engagement={DemoSeed.Coke}");
        var row = trail.EnumerateArray().Last(r => r.GetProperty("action").GetString() == "admin.member.set");
        Assert.Contains(id.ToString(), row.GetProperty("reason").GetString());
        var people = await admin.GetFromJsonAsync<JsonElement>("/admin/people");
        var listed = people.EnumerateArray().Single(p => p.GetProperty("id").GetGuid() == id);
        Assert.Equal("Staff", listed.GetProperty("engagements")[0].GetProperty("role").GetString());
        Assert.DoesNotContain(people.EnumerateArray(), p => p.GetProperty("id").GetGuid() == DemoSeed.Dana);  // Firm B's

        // Off Coke: out again at the very next call.
        Assert.Equal(HttpStatusCode.NoContent, (await admin.DeleteAsync($"/admin/engagements/{DemoSeed.Coke}/members/{id}")).StatusCode);
        Assert.Equal(HttpStatusCode.Forbidden, (await person.GetAsync($"/audit?engagement={DemoSeed.Coke}")).StatusCode);

        // Firm B's engagement, Firm B's person: not found, nothing changed.
        Assert.Equal(HttpStatusCode.NotFound, (await admin.PutAsJsonAsync($"/admin/engagements/{DemoSeed.Acme}/members/{id}", new { role = "Staff" })).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await admin.PutAsJsonAsync($"/admin/engagements/{DemoSeed.Coke}/members/{DemoSeed.Dana}", new { role = "Admin" })).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await admin.PutAsJsonAsync($"/admin/people/{DemoSeed.Dana}/entra", new { objectId = Guid.NewGuid() })).StatusCode);
        Assert.Null(warden.WithDb(db => db.Members.Find(DemoSeed.Coke, DemoSeed.Dana)));

        // Adding the person is logged firm-wide (no engagement).
        Assert.True(warden.WithDb(db => db.AuditLog.Any(a => a.Action == "admin.person.add" && a.EngagementId == null && a.Reason.Contains(id.ToString()))));
    }

    // A Firm A admin, signed in at the admin page.
    HttpClient AdminClient()
    {
        var (_, oid) = warden.NewLinkedCokeStaff();
        return With(WardenFactory.EntraToken(oid, azp: WardenFactory.FakeEntraClient, roles: Admin));
    }

    [Fact]
    public async Task an_ethical_wall_set_by_the_admin_beats_every_role()
    {
        var admin = AdminClient();
        var opened = await admin.PostAsJsonAsync("/admin/engagements", new { name = $"Fanta {Guid.NewGuid():N}" });
        Assert.Equal(HttpStatusCode.Created, opened.StatusCode);
        var fanta = (await opened.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetGuid();
        var (user, oid) = warden.NewLinkedCokeStaff();
        var person = With(WardenFactory.EntraToken(oid));
        await admin.PutAsJsonAsync($"/admin/engagements/{fanta}/members/{user}", new { role = "Admin" });
        Assert.Equal(HttpStatusCode.OK, (await person.GetAsync($"/audit?engagement={fanta}")).StatusCode);

        Assert.Equal(HttpStatusCode.BadRequest, (await admin.PutAsJsonAsync($"/admin/engagements/{fanta}/walls/{user}", new { reason = " " })).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await admin.PutAsJsonAsync($"/admin/engagements/{fanta}/walls/{user}", new { reason = "Audits Fanta's rival" })).StatusCode);
        var walled = await person.GetAsync($"/audit?engagement={fanta}");
        Assert.Equal(HttpStatusCode.Forbidden, walled.StatusCode);
        Assert.Contains("ethical wall: Audits Fanta's rival", await walled.Content.ReadAsStringAsync());
        var listed = (await admin.GetFromJsonAsync<JsonElement>("/admin/engagements")).EnumerateArray().Single(e => e.GetProperty("id").GetGuid() == fanta);
        Assert.Equal(user, listed.GetProperty("walls")[0].GetProperty("userId").GetGuid());

        Assert.Equal(HttpStatusCode.NoContent, (await admin.DeleteAsync($"/admin/engagements/{fanta}/walls/{user}")).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await person.GetAsync($"/audit?engagement={fanta}")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await admin.PutAsJsonAsync($"/admin/engagements/{DemoSeed.Acme}/walls/{DemoSeed.Dana}", new { reason = "x" })).StatusCode);
        Assert.DoesNotContain((await admin.GetFromJsonAsync<JsonElement>("/admin/engagements")).EnumerateArray(), e => e.GetProperty("id").GetGuid() == DemoSeed.Acme);
    }

    [Fact]
    public async Task an_agent_the_admin_allows_works_for_that_firm_only_up_to_its_ceiling()
    {
        var admin = AdminClient();
        var app = Guid.NewGuid();  // a new agent app, e.g. the firm's own agent
        Task<HttpResponseMessage> Exchange(Guid user, Guid firm, Guid engagement, string scope) =>
            warden.ClientFor(user, firm, app).PostAsJsonAsync("/token/exchange", new { resource = engagement, scope });

        Assert.Equal(HttpStatusCode.Forbidden, (await Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read")).StatusCode);  // not allowed yet
        Assert.Equal(HttpStatusCode.BadRequest, (await admin.PutAsJsonAsync($"/admin/agents/{app}", new { name = "Auditbot", allowedScopes = "documents.read root" })).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await admin.PutAsJsonAsync($"/admin/agents/{app}", new { name = "Auditbot", allowedScopes = "documents.read" })).StatusCode);

        Assert.Equal(HttpStatusCode.OK, (await Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read")).StatusCode);
        var beyond = await Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "vouch.run");  // Sara may vouch; Auditbot may not
        Assert.Contains("Auditbot is not registered for: vouch.run", await beyond.Content.ReadAsStringAsync());
        var otherFirm = await Exchange(DemoSeed.Dana, DemoSeed.FirmB, DemoSeed.Acme, "documents.read");  // Firm B never allowed it
        Assert.Contains("unknown agent", await otherFirm.Content.ReadAsStringAsync());

        // Ceiling to nothing = off: refused from the next ask on.
        await admin.PutAsJsonAsync($"/admin/agents/{app}", new { name = "Auditbot", allowedScopes = "" });
        Assert.Equal(HttpStatusCode.Forbidden, (await Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read")).StatusCode);
        // Each layer on its own (either alone stops Firm B; both are kept, both are tested):
        // the sign-in maps to the user's FIRM's registration of the app (Tickmark has one in each firm)...
        var danaPass = new Microsoft.IdentityModel.JsonWebTokens.JsonWebToken(await warden.Pass(DemoSeed.Dana, DemoSeed.FirmB, DemoSeed.Acme, "documents.read"));
        Assert.Equal(DemoSeed.TickmarkFirmB.ToString(), danaPass.GetPayloadValue<Dictionary<string, object>>("act")["sub"].ToString());
        // ...and the decision refuses another firm's registration even if it were handed one.
        Assert.Equal("unknown agent", warden.WithDb(db => Warden.Api.Auth.Decision.Decide(db, DemoSeed.FirmB, DemoSeed.Dana, DemoSeed.Tickmark,
            DemoSeed.Acme, new HashSet<string> { "documents.read" })).Reason);

        var agents = await admin.GetFromJsonAsync<JsonElement>("/admin/agents");
        Assert.DoesNotContain(agents.EnumerateArray(), a => a.GetProperty("id").GetGuid() == DemoSeed.TickmarkFirmB);  // Firm B's own registration
    }

    [Fact]
    public async Task a_connected_servers_tool_stays_hidden_until_the_admin_assigns_it()
    {
        warden.Upstream.Reset();
        warden.WithDb(db => { db.UpstreamTools.RemoveRange(db.UpstreamTools.Where(t => t.TenantId == DemoSeed.FirmA && t.Name == "delete_all")); return db.SaveChanges(); });
        var admin = AdminClient();
        Assert.Equal(HttpStatusCode.BadRequest, (await admin.PostAsJsonAsync("/admin/servers", new { name = "x", url = "ftp://files.test", audience = "x" })).StatusCode);
        Assert.Equal(HttpStatusCode.Conflict, (await admin.PostAsJsonAsync("/admin/servers", new { name = "copy", url = FakeUpstream.Url, audience = "tickmark-tools" })).StatusCode);
        var added = await admin.PostAsJsonAsync("/admin/servers", new { name = $"docs-{Guid.NewGuid():N}", url = FakeUpstream.Url, audience = $"aud-{Guid.NewGuid():N}" });
        Assert.Equal(HttpStatusCode.Created, added.StatusCode);
        var server = (await added.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetGuid();

        // What it offers, as the server itself says: delete_all is there, with no scope (hidden).
        var offered = await admin.GetFromJsonAsync<JsonElement>($"/admin/servers/{server}/tools");
        var deleteAll = offered.EnumerateArray().Single(t => t.GetProperty("name").GetString() == "delete_all");
        Assert.Equal(JsonValueKind.Null, deleteAll.GetProperty("scope").ValueKind);
        var pass = await warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");
        Assert.Contains("unknown tool: delete_all", (await warden.Invoke(pass, "delete_all")).Text);

        Assert.Equal(HttpStatusCode.BadRequest, (await admin.PutAsJsonAsync($"/admin/servers/{server}/tools/delete_all", new { scope = "root" })).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await admin.PutAsJsonAsync($"/admin/servers/{server}/tools/delete_all", new { scope = "documents.read" })).StatusCode);
        var call = await warden.Invoke(pass, "delete_all");
        Assert.Equal("called delete_all", call.Text);
        Assert.Equal(FakeUpstream.Url, warden.Upstream.Calls.Single().Url);

        Assert.Equal(HttpStatusCode.NoContent, (await admin.DeleteAsync($"/admin/servers/{server}/tools/delete_all")).StatusCode);
        Assert.Contains("unknown tool: delete_all", (await warden.Invoke(pass, "delete_all")).Text);
        // Firm B's server: not found, whatever Firm A's admin tries.
        var firmB = warden.WithDb(db =>
        {
            var b = new UpstreamServer { TenantId = DemoSeed.FirmB, Name = $"b-{Guid.NewGuid():N}", Url = FakeUpstream.Url, Audience = $"aud-{Guid.NewGuid():N}" };
            db.UpstreamServers.Add(b);
            db.SaveChanges();
            return b.Id;
        });
        Assert.Equal(HttpStatusCode.NotFound, (await admin.GetAsync($"/admin/servers/{firmB}/tools")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await admin.PutAsJsonAsync($"/admin/servers/{firmB}/tools/delete_all", new { scope = "documents.read" })).StatusCode);
        Assert.DoesNotContain((await admin.GetFromJsonAsync<JsonElement>("/admin/servers")).EnumerateArray(), x => x.GetProperty("id").GetGuid() == firmB);
    }

    [Fact]
    public async Task the_admin_audit_log_is_the_whole_firm_newest_first_and_paged()
    {
        var admin = AdminClient();
        await warden.Exchange(DemoSeed.Dana, DemoSeed.FirmB, DemoSeed.Tickmark, DemoSeed.Coke, "documents.read");  // Firm B's row
        await admin.PostAsJsonAsync("/admin/engagements", new { name = $"Sprite {Guid.NewGuid():N}" });

        var page = (await admin.GetFromJsonAsync<JsonElement>("/admin/audit?limit=2")).EnumerateArray().ToList();
        Assert.Equal(2, page.Count);
        Assert.Equal("admin.engagement.add", page[0].GetProperty("action").GetString());  // the newest
        Assert.True(page[0].GetProperty("id").GetInt64() > page[1].GetProperty("id").GetInt64());
        var next = (await admin.GetFromJsonAsync<JsonElement>($"/admin/audit?limit=2&before={page[1].GetProperty("id").GetInt64()}")).EnumerateArray();
        Assert.All(next, r => Assert.True(r.GetProperty("id").GetInt64() < page[1].GetProperty("id").GetInt64()));

        var all = (await admin.GetFromJsonAsync<JsonElement>("/admin/audit?limit=500")).EnumerateArray().ToList();
        Assert.DoesNotContain(all, r => r.GetProperty("userId").GetGuid() == DemoSeed.Dana);
        var coke = (await admin.GetFromJsonAsync<JsonElement>($"/admin/audit?engagement={DemoSeed.Coke}&limit=500")).EnumerateArray();
        Assert.All(coke, r => Assert.Equal(DemoSeed.Coke, r.GetProperty("engagementId").GetGuid()));
    }

    [Fact]
    public async Task the_admin_page_is_public_and_says_where_to_sign_in()
    {
        var client = warden.CreateClient();
        var page = await client.GetAsync("/admin/");
        Assert.Equal(HttpStatusCode.OK, page.StatusCode);
        var html = await page.Content.ReadAsStringAsync();
        Assert.Contains("script-src 'self'", html);  // no inline scripts may run
        // The app's bundle (admin-ui, built by Vite with a content hash in its name) is served too.
        var script = System.Text.RegularExpressions.Regex.Match(html, "src=\"\\./(assets/[^\"]+\\.js)\"").Groups[1].Value;
        Assert.NotEmpty(script);
        Assert.Equal(HttpStatusCode.OK, (await client.GetAsync($"/admin/{script}")).StatusCode);

        var signIn = await client.GetFromJsonAsync<JsonElement>("/admin/sign-in");
        Assert.Equal(WardenFactory.FakeEntraClient, signIn.GetProperty("clientId").GetString());
        Assert.Equal($"api://{WardenFactory.FakeEntraClient}/access_as_user", signIn.GetProperty("scope").GetString());
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/admin/people")).StatusCode);  // the data isn't public
    }
}
