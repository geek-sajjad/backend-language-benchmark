package bench.transform;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

import org.springframework.stereotype.Service;

import bench.transform.Cart.Discount;
import bench.transform.Cart.Fixed;
import bench.transform.Cart.LineItem;
import bench.transform.Cart.Percent;
import bench.transform.Cart.Pricing;

@Service
public class TransformService {
    private static final Pattern WHITESPACE = Pattern.compile("\\s+");

    private static final Comparator<TopItem> BY_TOTAL =
            Comparator.comparingLong(TopItem::totalCents).reversed().thenComparingInt(TopItem::lineId);
    private static final Comparator<CategorySummary> BY_SUBTOTAL =
            Comparator.comparingLong(CategorySummary::subtotalCents).reversed()
                    .thenComparing(CategorySummary::category);

    public record TopItem(int lineId, int productId, String name, long totalCents) {}

    public record CategorySummary(String category, int count, long subtotalCents, List<TopItem> topItems) {}

    public record CartSummary(String cartId, String currency, int itemCount, long totalCents,
                              List<CategorySummary> categories) {}

    /** Per-category accumulator while the cart is scanned. */
    private static final class Group {
        int count;
        long subtotal;
        final List<TopItem> items = new ArrayList<>();
    }

    /** Test 7: POST /transform */
    public CartSummary transform(Cart cart) {
        long total = 0;
        Map<String, Group> groups = new HashMap<>();
        for (LineItem item : cart.items()) {
            long lineTotal = (long) discountedUnit(item.pricing()) * item.quantity();
            total += lineTotal;
            Group group = groups.computeIfAbsent(item.product().category(), k -> new Group());
            group.count++;
            group.subtotal += lineTotal;
            group.items.add(new TopItem(item.lineId(), item.product().id(), normalizeName(item.product().name()),
                    lineTotal));
        }

        List<CategorySummary> categories = new ArrayList<>(groups.size());
        for (Map.Entry<String, Group> e : groups.entrySet()) {
            Group g = e.getValue();
            List<TopItem> top = g.items.stream().sorted(BY_TOTAL).limit(3).toList();
            categories.add(new CategorySummary(e.getKey(), g.count, g.subtotal, top));
        }
        // String.compareTo is plain code-unit order (names are ASCII), not locale order.
        categories.sort(BY_SUBTOTAL);
        return new CartSummary(cart.cartId(), cart.currency(), cart.items().size(), total, categories);
    }

    /** Discounts apply to the unit price in order; integer cents, rounding toward zero. */
    private static int discountedUnit(Pricing pricing) {
        int unit = pricing.unit();
        for (Discount d : pricing.discounts()) {
            unit = switch (d) {
                case Percent p -> unit - unit * p.value() / 100;
                case Fixed f -> Math.max(0, unit - f.value());
            };
        }
        return unit;
    }

    /** Trim, lowercase and collapse whitespace runs to a single space. */
    private static String normalizeName(String name) {
        return WHITESPACE.matcher(name.strip().toLowerCase(Locale.ROOT)).replaceAll(" ");
    }
}
