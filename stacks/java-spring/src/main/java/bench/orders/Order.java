package bench.orders;

import java.time.Instant;

import org.hibernate.annotations.CurrentTimestamp;
import org.hibernate.generator.EventType;
import org.hibernate.annotations.SourceType;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "orders")
public class Order {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private int customerId;
    private String status;
    private int totalCents;
    /** The DB's now() at insert, read back with the generated id. */
    @CurrentTimestamp(event = EventType.INSERT, source = SourceType.DB)
    private Instant createdAt;

    protected Order() {}

    public Order(int customerId, String status, int totalCents) {
        this.customerId = customerId;
        this.status = status;
        this.totalCents = totalCents;
    }

    public Long getId() { return id; }
    public int getCustomerId() { return customerId; }
    public String getStatus() { return status; }
    public int getTotalCents() { return totalCents; }
    public Instant getCreatedAt() { return createdAt; }
}
