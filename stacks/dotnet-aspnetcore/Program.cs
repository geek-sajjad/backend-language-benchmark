using System.Text.Json;
using System.Text.Json.Serialization;
using Bench.Data;
using Bench.Endpoints;
using Bench.Infrastructure;
using Microsoft.EntityFrameworkCore;
using Npgsql;

var builder = WebApplication.CreateBuilder(args);
var config = builder.Configuration;

// Startup order: JWT key → sales dataset → DB pool, all before Kestrel listens, so /health = ready.
var jwtKey = Auth.LoadPublicKey(config["JWT_PUBLIC_KEY_FILE"] ?? "/run/secrets/jwt_public.pem");
builder.Services.AddSingleton(Sales.Build());

var connectionString = new NpgsqlConnectionStringBuilder
{
    Host = config["DB_HOST"] ?? "127.0.0.1",
    Port = int.Parse(config["DB_PORT"] ?? "5432"),
    Database = config["DB_NAME"] ?? "bench",
    Username = config["DB_USER"] ?? "bench",
    Password = config["DB_PASSWORD"] ?? "bench",
    MaxPoolSize = int.Parse(config["DB_POOL_SIZE"] ?? "64"),
}.ConnectionString;
builder.Services.AddDbContext<BenchDb>(o => o.UseNpgsql(connectionString).UseSnakeCaseNamingConvention());

var downstreamUrl = (config["DOWNSTREAM_URL"] ?? "http://127.0.0.1:9000").TrimEnd('/') + "/";
builder.Services.AddHttpClient<InventoryClient>(c => c.BaseAddress = new Uri(downstreamUrl));

builder.Services.ConfigureHttpJsonOptions(o =>
{
    o.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
    // The web defaults read "42" as an integer; test 5 requires a 422 for it.
    o.SerializerOptions.NumberHandling = JsonNumberHandling.Strict;
});

builder.Services.AddValidation();
// Body-binding failures (malformed JSON, wrong types) throw, so ApiErrors can answer 422 instead of a bare 400.
builder.Services.Configure<RouteHandlerOptions>(o => o.ThrowOnBadRequest = true);
builder.Services.AddExceptionHandler<ApiErrors>();
builder.Services.AddProblemDetails(o => o.CustomizeProblemDetails = ApiErrors.ValidationAs422);

builder.Services.AddAuthentication().AddJwtBearer(o => Auth.Configure(o, jwtKey));
builder.Services.AddAuthorization();

var app = builder.Build();
await WaitForDatabase(app);

app.UseExceptionHandler();

app.MapGet("/health", () => "ok");

var api = app.MapGroup("");
MapApi(api);
api.MapGet("/customers/{id}/orders", Orders.CustomerOrders);
api.MapPost("/orders", Orders.Create);

var secure = app.MapGroup("/secure").RequireAuthorization();
MapApi(secure);
secure.MapGet("/customers/{id}/orders", Orders.SecureCustomerOrders);
secure.MapPost("/orders", Orders.SecureCreate);

app.Run();

// Routes served both unprotected (tests 2–8) and under /secure (test 9).
static void MapApi(RouteGroupBuilder g)
{
    g.MapGet("/products/{id}", Catalog.GetProduct);
    g.MapGet("/categories/{id}/products", Catalog.CategoryProducts);
    g.MapGet("/downstream/{id}", Downstream.Inventory);
    g.MapPost("/transform", Transform.Run);
    g.MapGet("/aggregate", Aggregate.Run);
}

// The DB may still be starting (compose up, DB reset): retry for up to a minute.
static async Task WaitForDatabase(WebApplication app)
{
    var log = app.Services.GetRequiredService<ILogger<BenchDb>>();
    for (var attempt = 1; ; attempt++)
    {
        await using var scope = app.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BenchDb>();
        try
        {
            await db.Database.OpenConnectionAsync();
            await db.Database.CloseConnectionAsync();
            return;
        }
        catch (NpgsqlException e) when (attempt < 60)
        {
            log.LogWarning("database not reachable yet ({Message}), retrying", e.Message);
            await Task.Delay(TimeSpan.FromSeconds(1));
        }
    }
}
