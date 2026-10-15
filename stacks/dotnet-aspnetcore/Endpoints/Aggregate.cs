using Bench.Data;
using Microsoft.AspNetCore.Mvc;

namespace Bench.Endpoints;

public class SalesGroup(int category, int amount)
{
    public int Category { get; } = category;
    public long Count { get; set; }
    public long SumAmount { get; set; }
    public int MinAmount { get; set; } = amount;
    public int MaxAmount { get; set; } = amount;
    public long AvgAmount { get; set; }
    public long TotalQuantity { get; set; }
}

public record AggregateResult(int Region, int MinAmount, List<SalesGroup> Groups);

public static class Aggregate
{
    // Test 8: GET /aggregate?region={r}&min_amount={m}
    public static AggregateResult Run(int region, [FromQuery(Name = "min_amount")] int minAmount, [FromServices] Sale[] sales)
    {
        var groups = new Dictionary<int, SalesGroup>();
        foreach (ref readonly var s in sales.AsSpan())
        {
            if (s.Region != region || s.AmountCents < minAmount)
            {
                continue;
            }
            if (!groups.TryGetValue(s.Category, out var g))
            {
                g = new SalesGroup(s.Category, s.AmountCents);
                groups[s.Category] = g;
            }
            g.Count++;
            g.SumAmount += s.AmountCents;
            g.MinAmount = Math.Min(g.MinAmount, s.AmountCents);
            g.MaxAmount = Math.Max(g.MaxAmount, s.AmountCents);
            g.TotalQuantity += s.Quantity;
        }

        var result = new List<SalesGroup>(groups.Values);
        foreach (var g in result)
        {
            g.AvgAmount = g.SumAmount / g.Count;
        }
        result.Sort((x, y) => x.SumAmount != y.SumAmount ? y.SumAmount.CompareTo(x.SumAmount) : x.Category.CompareTo(y.Category));
        if (result.Count > 10)
        {
            result.RemoveRange(10, result.Count - 10);
        }
        return new AggregateResult(region, minAmount, result);
    }
}
