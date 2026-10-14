package bench.orders;

import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;

public interface OrderRepository extends JpaRepository<Order, Long> {

    /** Test 4: the customer's 10 latest orders. */
    List<Order> findTop10ByCustomerIdOrderByCreatedAtDescIdDesc(int customerId);
}
