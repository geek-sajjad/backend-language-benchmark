package bench.orders;

import java.util.Collection;
import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface OrderItemRepository extends JpaRepository<OrderItem, Long> {

    /** Test 4: every item of the given orders with its product name, in one join. */
    @Query("""
            select new bench.orders.ItemRow(
                i.order.id, p.id, p.name, i.unitPriceCents, i.quantity)
            from OrderItem i join i.product p
            where i.order.id in :orderIds
            order by i.id""")
    List<ItemRow> findItemRows(Collection<Long> orderIds);
}
