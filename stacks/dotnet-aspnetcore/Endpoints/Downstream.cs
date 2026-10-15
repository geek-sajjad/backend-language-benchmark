using Bench.Infrastructure;

namespace Bench.Endpoints;

public record InventoryResponse(long Available);

public record Stock(long ProductId, long Available, string Source);

// Typed client from IHttpClientFactory: pooled SocketsHttpHandler, keep-alive, no per-host connection cap.
public class InventoryClient(HttpClient http)
{
    public Task<InventoryResponse?> Get(long id, CancellationToken ct) =>
        http.GetFromJsonAsync<InventoryResponse>($"inventory/{id}", ct);
}

public static class Downstream
{
    // Test 6: GET /downstream/{id}
    public static async Task<IResult> Inventory(long id, InventoryClient client, ILogger<InventoryClient> log, CancellationToken ct)
    {
        try
        {
            var inventory = await client.Get(id, ct) ?? throw new System.Text.Json.JsonException("empty body");
            return Results.Ok(new Stock(id, inventory.Available, "downstream"));
        }
        catch (Exception e) when (e is HttpRequestException or System.Text.Json.JsonException)
        {
            log.LogError(e, "downstream call failed");
            return ApiErrors.BadGateway();
        }
    }
}
