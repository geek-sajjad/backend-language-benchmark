package bench.catalog;

import java.util.Collection;
import java.util.List;

import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;

// Query methods run in Spring Data's default read-only transaction, which also makes the
// Hibernate session read-only (no snapshots kept for dirty checking).
public interface ProductRepository extends JpaRepository<Product, Integer> {

    /** Test 3: one keyset page of full entities. */
    List<Product> findByCategoryIdAndIdGreaterThanOrderById(int categoryId, int after, Limit limit);

    /** Test 5: the order's products, ascending id. */
    List<Product> findByIdInOrderById(Collection<Integer> ids);

    /** Test 5: atomic decrement, no load-modify-save. */
    @Modifying
    @Query("update Product p set p.stock = p.stock - :quantity where p.id = :id")
    int decrementStock(int id, int quantity);
}
