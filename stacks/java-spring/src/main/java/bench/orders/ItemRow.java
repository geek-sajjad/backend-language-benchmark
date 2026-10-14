package bench.orders;

/** Test 4: one order item with its product name (a JPQL constructor projection). */
public record ItemRow(long orderId, int productId, String name, int unitPriceCents, int quantity) {}
