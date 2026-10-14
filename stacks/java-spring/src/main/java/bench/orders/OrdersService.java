package bench.orders;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import bench.catalog.Product;
import bench.catalog.ProductRepository;
import bench.orders.CreateOrderRequest.OrderLine;
import bench.web.ValidationFailedException;

@Service
public class OrdersService {

    public record ItemView(int productId, String name, int unitPriceCents, int quantity) {}

    public record OrderView(long id, String status, int totalCents, Instant createdAt, List<ItemView> items) {}

    public record CustomerOrders(int customerId, List<OrderView> orders) {}

    private final OrderRepository orders;
    private final OrderItemRepository items;
    private final ProductRepository products;

    public OrdersService(OrderRepository orders, OrderItemRepository items, ProductRepository products) {
        this.orders = orders;
        this.items = items;
        this.products = products;
    }

    /** Test 4: 2 queries: the 10 latest orders, then all their items with product names (`IN`). */
    @Transactional(readOnly = true)
    public CustomerOrders customerOrders(int customerId) {
        List<Order> latest = orders.findTop10ByCustomerIdOrderByCreatedAtDescIdDesc(customerId);
        Map<Long, List<ItemView>> itemsByOrder = new HashMap<>();
        if (!latest.isEmpty()) {
            for (ItemRow row : items.findItemRows(latest.stream().map(Order::getId).toList())) {
                itemsByOrder.computeIfAbsent(row.orderId(), k -> new ArrayList<>())
                        .add(new ItemView(row.productId(), row.name(), row.unitPriceCents(), row.quantity()));
            }
        }
        List<OrderView> views = latest.stream()
                .map(o -> new OrderView(o.getId(), o.getStatus(), o.getTotalCents(), o.getCreatedAt(),
                        itemsByOrder.getOrDefault(o.getId(), List.of())))
                .toList();
        return new CustomerOrders(customerId, views);
    }

    /**
     * Test 5: one transaction: read prices, insert the order and its items, decrement stock atomically.
     * Items are handled in ascending product_id order so concurrent orders lock rows in the same order.
     * An unknown customer fails the order insert (FK violation), which the error handler turns into a 422.
     */
    @Transactional
    public OrderView createOrder(int customerId, List<OrderLine> lines) {
        lines = lines.stream().sorted(Comparator.comparingInt(OrderLine::productId)).toList();
        List<Product> found = products.findByIdInOrderById(lines.stream().map(OrderLine::productId).toList());
        if (found.size() != lines.size()) {
            throw new ValidationFailedException(Map.of("items", "unknown product_id"));
        }

        int total = 0;
        for (int i = 0; i < lines.size(); i++) {
            total += found.get(i).getPriceCents() * lines.get(i).quantity();
        }
        Order order = orders.save(new Order(customerId, "pending", total));

        List<OrderItem> newItems = new ArrayList<>(lines.size());
        List<ItemView> views = new ArrayList<>(lines.size());
        for (int i = 0; i < lines.size(); i++) {
            Product p = found.get(i);
            int quantity = lines.get(i).quantity();
            newItems.add(new OrderItem(order, p, quantity, p.getPriceCents()));
            views.add(new ItemView(p.getId(), p.getName(), p.getPriceCents(), quantity));
        }
        items.saveAll(newItems);

        for (OrderLine line : lines) {
            products.decrementStock(line.productId(), line.quantity());
        }
        return new OrderView(order.getId(), order.getStatus(), total, order.getCreatedAt(), views);
    }
}
