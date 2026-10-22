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
 * Check(...) is the other half: "is this a real, current pass for the gate?" (used by step 9).
 *   Layer 1, the stamp:   signed by Warden (checked with the PUBLIC key), iss = warden,
 *                         aud = warden-tools, not expired (no grace period).
 *   Layer 2, the logbook: the jti is in delegated_tokens and RevokedAt is empty.
 *   Any failure -> not OK, with a short reason. On success it returns the logbook row,
 *   which says exactly who, which agent, which engagement and which scopes.
 */
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using Warden.Api.Data;
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

    // The result of checking a pass: OK + the logbook row, or not OK + why.
    public record CheckResult(bool Ok, string? Reason, DelegatedToken? Row);

    public static async Task<CheckResult> Check(string token, WardenSigningKey key, WardenDbContext db)
    {
        // Layer 1: the stamp.
        var result = await new JsonWebTokenHandler().ValidateTokenAsync(token, new TokenValidationParameters
        {
            ValidIssuer = Issuer,
            ValidAudience = Audience,
            IssuerSigningKey = key.PublicJwk,  // public key only: checking never needs the private one
            ClockSkew = TimeSpan.Zero,         // expired means expired
        });
        if (!result.IsValid)
            return Fail(result.Exception switch
            {
                SecurityTokenExpiredException => "pass expired",
                SecurityTokenInvalidAudienceException => "pass not meant for the tool gate",
                SecurityTokenInvalidIssuerException => "pass not issued by Warden",
                SecurityTokenSignatureKeyNotFoundException or SecurityTokenInvalidSignatureException => "bad signature",
                _ => "invalid pass",
            });

        // Layer 2: the logbook. A signed pass Warden has no record of, or has revoked, is refused.
        if (!Guid.TryParse(result.ClaimsIdentity.FindFirst("jti")?.Value, out var jti))
            return Fail("pass has no id");
        var row = await db.DelegatedTokens.FindAsync(jti);
        if (row is null) return Fail("pass not in logbook");
        if (row.RevokedAt is not null) return Fail("pass revoked");

        return new CheckResult(true, null, row);
    }

    static CheckResult Fail(string reason) => new(false, reason, null);
}
