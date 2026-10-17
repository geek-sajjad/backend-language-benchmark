package bench.transform;

import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

/**
 * Test 7 request body: a cart export. Bound by Jackson only, with no Bean Validation: test 7
 * validates nothing beyond parsing (docs/02-test-spec.md, test 7).
 */
public record Cart(String cartId, String currency, List<LineItem> items) {

    public record LineItem(int lineId, int quantity, CartProduct product, Pricing pricing) {}

    public record CartProduct(int id, String name, String category, Map<String, String> attributes) {}

    public record Pricing(int unit, String currency, List<Discount> discounts) {}

    /** `{"kind": "percent" | "fixed", "value": n}` */
    @JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "kind")
    @JsonSubTypes({
            @JsonSubTypes.Type(value = Percent.class, name = "percent"),
            @JsonSubTypes.Type(value = Fixed.class, name = "fixed")})
    public sealed interface Discount permits Percent, Fixed {}

    public record Percent(int value) implements Discount {}

    public record Fixed(int value) implements Discount {}
}
