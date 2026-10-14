package bench.catalog;

import java.time.Instant;
import java.util.List;

import org.springframework.data.domain.Limit;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
public class CatalogService {
    private static final Limit PAGE_SIZE = Limit.of(50);

    public record ProductSummary(int id, String sku, String name, int priceCents, int stock, Instant createdAt) {}

    public record CategoryPage(int categoryId, Integer nextAfter, List<ProductSummary> items) {}

    private final ProductRepository products;

    public CatalogService(ProductRepository products) {
        this.products = products;
    }

    /** Test 2: one product by primary key, every column. */
    public Product getProduct(int id) {
        return products.findById(id).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
    }

    /** Test 3: one keyset page of 50 full entities; `description` is loaded but not returned. */
    public CategoryPage categoryPage(int categoryId, int after) {
        List<ProductSummary> items = products.findByCategoryIdAndIdGreaterThanOrderById(categoryId, after, PAGE_SIZE)
                .stream()
                .map(p -> new ProductSummary(p.getId(), p.getSku(), p.getName(), p.getPriceCents(), p.getStock(),
                        p.getCreatedAt()))
                .toList();
        Integer nextAfter = items.isEmpty() ? null : items.getLast().id();
        return new CategoryPage(categoryId, nextAfter, items);
    }
}
