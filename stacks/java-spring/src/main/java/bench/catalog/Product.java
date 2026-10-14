package bench.catalog;

import java.time.Instant;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/** Serialized as is for test 2 (every column). */
@Entity
@Table(name = "products")
public class Product {
    @Id
    private int id;
    private String sku;
    private String name;
    private String description;
    private int priceCents;
    private int stock;
    private int categoryId;
    private Instant createdAt;

    protected Product() {}

    public int getId() { return id; }
    public String getSku() { return sku; }
    public String getName() { return name; }
    public String getDescription() { return description; }
    public int getPriceCents() { return priceCents; }
    public int getStock() { return stock; }
    public int getCategoryId() { return categoryId; }
    public Instant getCreatedAt() { return createdAt; }
}
