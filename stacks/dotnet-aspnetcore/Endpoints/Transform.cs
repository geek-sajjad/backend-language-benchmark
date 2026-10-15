namespace Bench.Endpoints;

public record Cart(string CartId, string Currency, List<LineItem> Items);

public record LineItem(long LineId, CartProduct Product, Pricing Pricing, long Quantity);

public record CartProduct(long Id, string Name, string Category, Dictionary<string, string> Attributes);

public record Pricing(long Unit, string Currency, List<Discount> Discounts);

public record Discount(string Kind, long Value); // percent | fixed

public record TopItem(long LineId, long ProductId, string Name, long TotalCents);

public record CategorySummary(string Category, int Count, long SubtotalCents, List<TopItem> TopItems);

public record CartSummary(string CartId, string Currency, int ItemCount, long TotalCents, List<CategorySummary> Categories);

public static class Transform
{
    // Test 7: POST /transform
    public static CartSummary Run(Cart cart)
    {
        long total = 0;
        var groups = new Dictionary<string, (int Count, long Subtotal, List<TopItem> Items)>();
        foreach (var item in cart.Items)
        {
            var lineTotal = DiscountedUnit(item.Pricing) * item.Quantity;
            total += lineTotal;
            var (count, subtotal, items) = groups.GetValueOrDefault(item.Product.Category, (0, 0, []));
            items.Add(new TopItem(item.LineId, item.Product.Id, NormalizeName(item.Product.Name), lineTotal));
            groups[item.Product.Category] = (count + 1, subtotal + lineTotal, items);
        }

        var categories = groups
            .Select(g => new CategorySummary(g.Key, g.Value.Count, g.Value.Subtotal,
                g.Value.Items.OrderByDescending(i => i.TotalCents).ThenBy(i => i.LineId).Take(3).ToList()))
            .OrderByDescending(c => c.SubtotalCents).ThenBy(c => c.Category, StringComparer.Ordinal)
            .ToList();

        return new CartSummary(cart.CartId, cart.Currency, cart.Items.Count, total, categories);
    }

    // Applies the discounts to the unit price in order; integer cents, rounding toward zero.
    static long DiscountedUnit(Pricing pricing)
    {
        var unit = pricing.Unit;
        foreach (var d in pricing.Discounts)
        {
            unit = d.Kind switch
            {
                "percent" => unit - unit * d.Value / 100,
                "fixed" => Math.Max(0, unit - d.Value),
                _ => unit,
            };
        }
        return unit;
    }

    // Trims, lowercases and collapses whitespace runs to a single space.
    static string NormalizeName(string name) =>
        string.Join(' ', name.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)).ToLowerInvariant();
}
