/*
 * EntraTests.cs — step 14: Microsoft Entra ID sign-in, against a FAKE Entra (WardenFactory).
 *
 *   linked Entra account                 -> signed in as its Warden user (/me shows Warden's ids)
 *                                           and gets an agent pass: the same code path as dev tokens
 *   Entra account not linked to a user   -> 401
 *   token for another app (audience)     -> 401
 *   token from another Entra tenant      -> 401
 *   token signed by someone else's key   -> 401
 * The real Entra check (Microsoft's keys, a real app registration) is done by hand: docs/how-it-works.md, section 22.
 */
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.IdentityModel.Tokens;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class EntraTests(WardenFactory warden)
{
    (Guid user, Guid oid) LinkedUser() => warden.NewLinkedCokeStaff();

    HttpClient With(string token)
    {
        var client = warden.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client;
    }

    [Fact]
    public async Task linked_entra_account_signs_in_as_its_warden_user_and_gets_a_pass()
    {
        var (user, oid) = LinkedUser();
        var client = With(WardenFactory.EntraToken(oid));

        var me = await client.GetFromJsonAsync<JsonElement>("/me");
        Assert.Equal(user.ToString(), me.GetProperty("userId").GetString());              // Warden's id, not the oid
        Assert.Equal(DemoSeed.FirmA.ToString(), me.GetProperty("tenantId").GetString());  // Warden's firm, not Entra's tid

        var res = await client.PostAsJsonAsync("/token/exchange",
            new { resource = DemoSeed.Coke, scope = "documents.read" });
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
    }

    [Fact]
    public async Task unlinked_or_wrong_entra_tokens_are_401()
    {
        var (_, oid) = LinkedUser();
        string[] bad =
        [
            WardenFactory.EntraToken(Guid.NewGuid()),                                   // not linked
            WardenFactory.EntraToken(oid, audience: "some-other-app"),                  // not for Warden
            WardenFactory.EntraToken(oid, tid: Guid.NewGuid()),                         // another Entra tenant
            WardenFactory.EntraToken(oid, key: new RsaSecurityKey(RSA.Create(2048))),   // forged
        ];
        foreach (var token in bad)
            Assert.Equal(HttpStatusCode.Unauthorized, (await With(token).GetAsync("/me")).StatusCode);
    }
}
