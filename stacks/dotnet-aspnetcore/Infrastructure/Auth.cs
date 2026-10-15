using System.Globalization;
using System.Security.Claims;
using System.Security.Cryptography;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.IdentityModel.Tokens;

namespace Bench.Infrastructure;

public static class Auth
{
    // Read once at startup; no JWKS fetching (docs/02-test-spec.md, Authentication).
    public static RsaSecurityKey LoadPublicKey(string path)
    {
        var rsa = RSA.Create();
        rsa.ImportFromPem(File.ReadAllText(path));
        return new RsaSecurityKey(rsa);
    }

    // RS256 signature, exp (required), iss and aud, and a numeric sub, which handlers read with CustomerId.
    public static void Configure(JwtBearerOptions o, RsaSecurityKey key)
    {
        o.MapInboundClaims = false; // keep "sub" as "sub"
        o.TokenValidationParameters = new TokenValidationParameters
        {
            IssuerSigningKey = key,
            ValidAlgorithms = [SecurityAlgorithms.RsaSha256],
            ValidIssuer = "bench-idp",
            ValidAudience = "bench-api",
            RequireExpirationTime = true,
            RequireSignedTokens = true,
        };
        o.Events = new JwtBearerEvents
        {
            OnTokenValidated = ctx =>
            {
                if (!int.TryParse(ctx.Principal?.FindFirstValue("sub"), NumberStyles.None, CultureInfo.InvariantCulture, out _))
                {
                    ctx.Fail("sub must be a customer id");
                }
                return Task.CompletedTask;
            },
            // Spec error body instead of an empty 401.
            OnChallenge = async ctx =>
            {
                ctx.HandleResponse();
                await Results.Json(new { error = "unauthorized" }, statusCode: 401).ExecuteAsync(ctx.HttpContext);
            },
        };
    }

    // The customer id from the token's sub claim (validated in OnTokenValidated).
    public static int CustomerId(ClaimsPrincipal user) =>
        int.Parse(user.FindFirstValue("sub")!, CultureInfo.InvariantCulture);
}
