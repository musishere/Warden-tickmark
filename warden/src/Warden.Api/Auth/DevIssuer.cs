/*
 * DevIssuer.cs — a local, fake "sign-in service" for development and tests.
 *
 * In production, users sign in with Microsoft Entra ID, which hands them a signed
 * token (a JWT). Until we wire Entra in (step 14), this class stamps look-alike
 * tokens with a dev key so we can work offline.
 *
 * A JWT is three base64 parts: header.payload.signature
 *   payload   = the claims, e.g. { "oid": <user id>, "tid": <tenant id>, "name": "Sara", "exp": ... }
 *   signature = proof the payload was stamped with the key and not changed since.
 * We use Entra's claim names (oid = object id = user, tid = tenant id, azp = the app (agent) the
 * token was issued to, step 17a) so switching to Entra later changes only who stamps the token,
 * not the code that reads it. jti = this token's own id (Entra's is `uti`).
 *
 * Program.cs trusts this issuer ONLY in Development. In Production its tokens are rejected.
 */
using System.Text;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;

namespace Warden.Api.Auth;

public static class DevIssuer
{
    public const string Issuer = "warden-dev";   // "iss" claim: who stamped the token
    public const string Audience = "warden";     // "aud" claim: who the token is meant for

    // The dev key from appsettings.json ("DevIssuer:Key"), as a signing key object.
    public static SymmetricSecurityKey SigningKey(IConfiguration config) =>
        SigningKey(config["DevIssuer:Key"] ?? throw new InvalidOperationException("DevIssuer:Key is not set"));

    public static SymmetricSecurityKey SigningKey(string key) => new(Encoding.UTF8.GetBytes(key));

    // Stamp a token for a user, valid for the hour before `expires`, issued to the agent whose
    // Entra client id is `agentClientId` (none = a person signing in themselves, not an agent).
    // Tests pass tenantId = null (a broken token) or an `expires` in the past (an expired one).
    public static string CreateToken(SecurityKey key, Guid userId, Guid? tenantId, string name, DateTime expires, Guid? agentClientId = null)
    {
        var claims = new Dictionary<string, object> { ["oid"] = userId.ToString(), ["name"] = name, ["jti"] = Guid.NewGuid().ToString() };
        if (tenantId is not null) claims["tid"] = tenantId.Value.ToString();
        if (agentClientId is not null) claims["azp"] = agentClientId.Value.ToString();

        return new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
        {
            Issuer = Issuer,
            Audience = Audience,
            Claims = claims,
            IssuedAt = expires.AddHours(-1),
            NotBefore = expires.AddHours(-1),
            Expires = expires,
            SigningCredentials = new SigningCredentials(key, SecurityAlgorithms.HmacSha256),
        });
    }
}
