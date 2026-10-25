/*
 * WardenSigningKey.cs — Warden's own stamp, used to sign agent passes (step 6).
 *
 * It's an RSA key PAIR:
 *   private key — stays inside Warden, used to SIGN passes. Never leaves this class.
 *   public key  — published at GET /.well-known/jwks.json so Tickmark (or anyone) can
 *                 CHECK a pass's signature. It can verify, but it cannot forge.
 * (The dev issuer in DevIssuer.cs uses one shared secret for both; fine for tests,
 *  but here Tickmark would then hold a key that can forge passes. A key pair avoids that.)
 *
 * Where the private key comes from:
 *   config "Signing:PrivateKeyPem" set -> use it (production: from Key Vault / env var)
 *   else, in Development                -> generate a fresh one at startup (nothing to manage;
 *                                          passes live 15 minutes, so losing it on restart is fine)
 *   else (Production, no key)           -> throw: Warden refuses to sign (fail closed)
 *
 * Kid ("key id") is a fingerprint of the public key. Each pass says which kid signed it,
 * so a checker can pick the right key from the JWKS (useful when keys are rotated).
 *
 * Registered once for the whole app in Program.cs (AddSingleton = one shared instance).
 */
using System.Security.Cryptography;
using Microsoft.IdentityModel.Tokens;

namespace Warden.Api.Auth;

public class WardenSigningKey
{
    public RsaSecurityKey PrivateKey { get; }
    public JsonWebKey PublicJwk { get; }
    public string Kid => PublicJwk.Kid;
    public SigningCredentials Credentials => new(PrivateKey, SecurityAlgorithms.RsaSha256);

    public WardenSigningKey(IConfiguration config, IHostEnvironment env)
    {
        var rsa = RSA.Create(2048);
        var pem = config["Signing:PrivateKeyPem"];
        if (!string.IsNullOrEmpty(pem)) rsa.ImportFromPem(pem);
        else if (!env.IsDevelopment())
            throw new InvalidOperationException("Signing:PrivateKeyPem is not set: Warden will not sign passes without a real key.");
        // (Development with no PEM: keep the fresh key RSA.Create just generated.)

        // Public half only: ExportParameters(false) leaves out every private number.
        var publicKey = new RsaSecurityKey(rsa.ExportParameters(false));
        PublicJwk = JsonWebKeyConverter.ConvertFromRSASecurityKey(publicKey);
        PublicJwk.Kid = Base64UrlEncoder.Encode(PublicJwk.ComputeJwkThumbprint());
        PublicJwk.Use = "sig";
        PublicJwk.Alg = SecurityAlgorithms.RsaSha256;

        PrivateKey = new RsaSecurityKey(rsa) { KeyId = Kid };
    }
}
