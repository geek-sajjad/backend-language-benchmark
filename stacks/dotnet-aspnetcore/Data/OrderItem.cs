namespace Bench.Data;

public class OrderItem
{
    public long Id { get; set; }
    public long OrderId { get; set; }
    public int ProductId { get; set; }
    public int Quantity { get; set; }
    public int UnitPriceCents { get; set; }
    public Product Product { get; set; } = null!;
}
