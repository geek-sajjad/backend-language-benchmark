using Microsoft.EntityFrameworkCore;

namespace Bench.Data;

// Table and column names come from UseSnakeCaseNamingConvention (Products → products, PriceCents → price_cents).
public class BenchDb(DbContextOptions<BenchDb> options) : DbContext(options)
{
    public DbSet<Product> Products => Set<Product>();
    public DbSet<Order> Orders => Set<Order>();
    public DbSet<OrderItem> OrderItems => Set<OrderItem>();

    protected override void OnModelCreating(ModelBuilder model)
    {
        model.Entity<Order>().HasMany(o => o.Items).WithOne().HasForeignKey(i => i.OrderId);
        model.Entity<OrderItem>().HasOne(i => i.Product).WithMany().HasForeignKey(i => i.ProductId);
    }
}
