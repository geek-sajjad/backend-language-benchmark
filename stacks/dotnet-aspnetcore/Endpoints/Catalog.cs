using Bench.Data;
using Bench.Infrastructure;
using Microsoft.EntityFrameworkCore;

namespace Bench.Endpoints;

public record ProductSummary(int Id, string Sku, string Name, int PriceCents, int Stock, DateTime CreatedAt);

public record CategoryPage(int CategoryId, int? NextAfter, List<ProductSummary> Items);

public static class Catalog
{
    // Test 2: GET /products/{id}
    public static async Task<IResult> GetProduct(int id, BenchDb db, CancellationToken ct)
    {
        var product = await db.Products.AsNoTracking().FirstOrDefaultAsync(p => p.Id == id, ct);
        return product is null ? ApiErrors.NotFound() : Results.Ok(product);
    }

    // Test 3: GET /categories/{id}/products?after={after_id}. Full entities, mapped to the summary shape.
    public static async Task<IResult> CategoryProducts(int id, BenchDb db, CancellationToken ct, int after = 0)
    {
        var products = await db.Products.AsNoTracking()
            .Where(p => p.CategoryId == id && p.Id > after)
            .OrderBy(p => p.Id)
            .Take(50)
            .ToListAsync(ct);

        var items = products.ConvertAll(p => new ProductSummary(p.Id, p.Sku, p.Name, p.PriceCents, p.Stock, p.CreatedAt));
        return Results.Ok(new CategoryPage(id, items.Count > 0 ? items[^1].Id : null, items));
    }
}
