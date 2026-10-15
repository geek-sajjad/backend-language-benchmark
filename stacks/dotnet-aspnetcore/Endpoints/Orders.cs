using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using Bench.Data;
using Bench.Infrastructure;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace Bench.Endpoints;

public record OrderItemDto(int ProductId, string Name, int UnitPriceCents, int Quantity);

public record OrderDto(long Id, string Status, int TotalCents, DateTime CreatedAt, List<OrderItemDto> Items);

public record CustomerOrders(int CustomerId, List<OrderDto> Orders);

public class OrderLine
{
    [Range(1, int.MaxValue)]
    public int ProductId { get; init; }

    [Range(1, 10)]
    public int Quantity { get; init; }
}

// POST /secure/orders: any customer_id in the body is ignored; the token's sub is used.
public class SecureCreateOrderRequest : IValidatableObject
{
    [Required, Length(1, 10)]
    public List<OrderLine>? Items { get; init; }

    public IEnumerable<ValidationResult> Validate(ValidationContext context)
    {
        if (Items is not null && Items.DistinctBy(l => l.ProductId).Count() != Items.Count)
        {
            yield return new ValidationResult("duplicate product_id", [nameof(Items)]);
        }
    }
}

public class CreateOrderRequest : SecureCreateOrderRequest
{
    [Range(1, int.MaxValue)]
    public int CustomerId { get; init; }
}

public static class Orders
{
    // Test 4: GET /customers/{id}/orders. One query: the 10 newest orders, each with its items and
    // the product names (EF projection; only the product's name is read).
    public static async Task<IResult> CustomerOrders(int id, BenchDb db, CancellationToken ct) =>
        Results.Ok(new CustomerOrders(id, await LoadOrders(db, id, ct)));

    // Only the token's own customer may read their orders.
    public static async Task<IResult> SecureCustomerOrders(int id, ClaimsPrincipal user, BenchDb db, CancellationToken ct) =>
        id != Auth.CustomerId(user) ? ApiErrors.Forbidden() : await CustomerOrders(id, db, ct);

    static Task<List<OrderDto>> LoadOrders(BenchDb db, int customerId, CancellationToken ct) =>
        db.Orders
            .Where(o => o.CustomerId == customerId)
            .OrderByDescending(o => o.CreatedAt).ThenByDescending(o => o.Id)
            .Take(10)
            .Select(o => new OrderDto(o.Id, o.Status, o.TotalCents, o.CreatedAt,
                o.Items.OrderBy(i => i.Id)
                    .Select(i => new OrderItemDto(i.ProductId, i.Product.Name, i.UnitPriceCents, i.Quantity))
                    .ToList()))
            .ToListAsync(ct);

    // Test 5: POST /orders
    public static Task<IResult> Create(CreateOrderRequest req, BenchDb db, CancellationToken ct) =>
        Insert(db, req.CustomerId, req.Items!, ct);

    public static Task<IResult> SecureCreate(SecureCreateOrderRequest req, ClaimsPrincipal user, BenchDb db, CancellationToken ct) =>
        Insert(db, Auth.CustomerId(user), req.Items!, ct);

    // One transaction: read prices, insert the order and its items, decrement stock atomically.
    // Items are handled in ascending product_id order so concurrent orders lock rows in the same order.
    static async Task<IResult> Insert(BenchDb db, int customerId, List<OrderLine> lines, CancellationToken ct)
    {
        lines = [.. lines.OrderBy(l => l.ProductId)];
        var ids = lines.ConvertAll(l => l.ProductId);

        await using var tx = await db.Database.BeginTransactionAsync(ct);
        var products = await db.Products
            .Where(p => ids.Contains(p.Id))
            .OrderBy(p => p.Id)
            .Select(p => new { p.Id, p.Name, p.PriceCents })
            .ToListAsync(ct);
        if (products.Count != lines.Count)
        {
            return ApiErrors.ValidationFailed(new { items = "unknown product_id" });
        }

        // App clock (deviation, see README): the schema has no DEFAULT, and EF Core can't put now() into an INSERT.
        // Postgres stores microseconds; truncating keeps the 201 response equal to the stored row.
        var now = DateTime.UtcNow;
        var order = new Order { CustomerId = customerId, Status = "pending", CreatedAt = now.AddTicks(-(now.Ticks % 10)) };
        for (var i = 0; i < lines.Count; i++)
        {
            order.Items.Add(new OrderItem { ProductId = lines[i].ProductId, Quantity = lines[i].Quantity, UnitPriceCents = products[i].PriceCents });
            order.TotalCents += products[i].PriceCents * lines[i].Quantity;
        }
        db.Orders.Add(order);
        try
        {
            await db.SaveChangesAsync(ct);
        }
        catch (DbUpdateException e) when (e.InnerException is PostgresException { SqlState: PostgresErrorCodes.ForeignKeyViolation })
        {
            return ApiErrors.ValidationFailed(new { customer_id = "unknown customer" });
        }
        foreach (var line in lines)
        {
            await db.Products.Where(p => p.Id == line.ProductId)
                .ExecuteUpdateAsync(s => s.SetProperty(p => p.Stock, p => p.Stock - line.Quantity), ct);
        }
        await tx.CommitAsync(ct);

        var items = lines.Select((l, i) => new OrderItemDto(l.ProductId, products[i].Name, products[i].PriceCents, l.Quantity)).ToList();
        return Results.Json(new OrderDto(order.Id, order.Status, order.TotalCents, order.CreatedAt, items), statusCode: 201);
    }
}
