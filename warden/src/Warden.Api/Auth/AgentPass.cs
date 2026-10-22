/*
 * AgentPass.cs — what goes inside an agent pass, and how Warden stamps one.
 *
 * A pass is a JWT signed with Warden's private key (WardenSigningKey.cs). Inside:
 *   iss   "warden"                  who stamped it
 *   aud   "warden-tools"            where it may be used: Warden's tool gate (step 9)
 *   sub   Sara's user id            on whose behalf
 *   act   { "sub": Tickmark's id }  who is actually acting (standard RFC 8693 "actor" claim)
 *   tid   Firm A's id               which firm
 *   eng   Coke's id                 the ONLY engagement it works for
 *   scope "documents.read vouch.run" the ONLY actions it allows
 *   jti   the pass number           matches the row in delegated_tokens (the logbook)
 *   iat / nbf / exp                 issued / not before / expires (15 minutes)
 *
 * Step 6c adds the matching "check a pass" method here.
 */
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using Warden.Core.Domain;

namespace Warden.Api.Auth;

public static class AgentPass
{
    public const string Issuer = "warden";
    public const string Audience = "warden-tools";

    // Stamp the pass described by a logbook row.
    public static string Create(DelegatedToken row, WardenSigningKey key) =>
        new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
        {
            Issuer = Issuer,
            Audience = Audience,
            Claims = new Dictionary<string, object>
            {
                ["sub"] = row.UserId.ToString(),
                ["act"] = new Dictionary<string, object> { ["sub"] = row.AgentId.ToString() },
                ["tid"] = row.TenantId.ToString(),
                ["eng"] = row.EngagementId.ToString(),
                ["scope"] = row.Scope,
                ["jti"] = row.Jti.ToString(),
            },
            IssuedAt = row.IssuedAt,
            NotBefore = row.IssuedAt,
            Expires = row.ExpiresAt,
            SigningCredentials = key.Credentials,
        });
}
