namespace Bench.Data;

public class Order
{
    public long Id { get; set; }
    public int CustomerId { get; set; }
    public string Status { get; set; } = "";
    public int TotalCents { get; set; }
    public DateTime CreatedAt { get; set; }
    public List<OrderItem> Items { get; set; } = [];
}
