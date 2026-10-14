package bench.transform;

import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;

/** Test 7 request body: a cart export. */
public record Cart(
        @NotNull String cartId,
        @NotNull String currency,
        @NotNull List<@NotNull @Valid LineItem> items) {

    public record LineItem(
            @NotNull Integer lineId,
            @NotNull Integer quantity,
            @NotNull @Valid CartProduct product,
            @NotNull @Valid Pricing pricing) {}

    public record CartProduct(
            @NotNull Integer id,
            @NotNull String name,
            @NotNull String category,
            @NotNull Map<String, String> attributes) {}

    public record Pricing(
            @NotNull Integer unit,
            @NotNull String currency,
            @NotNull List<@NotNull @Valid Discount> discounts) {}

    /** `{"kind": "percent" | "fixed", "value": n}` */
    @JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "kind")
    @JsonSubTypes({
            @JsonSubTypes.Type(value = Percent.class, name = "percent"),
            @JsonSubTypes.Type(value = Fixed.class, name = "fixed")})
    public sealed interface Discount permits Percent, Fixed {}

    public record Percent(@NotNull Integer value) implements Discount {}

    public record Fixed(@NotNull Integer value) implements Discount {}
}
