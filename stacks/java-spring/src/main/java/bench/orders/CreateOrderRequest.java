package bench.orders;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import java.util.HashSet;
import java.util.List;

import jakarta.validation.Constraint;
import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import jakarta.validation.Payload;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import static java.lang.annotation.ElementType.FIELD;
import static java.lang.annotation.ElementType.METHOD;
import static java.lang.annotation.ElementType.PARAMETER;
import static java.lang.annotation.ElementType.RECORD_COMPONENT;

/** Test 5 request body: `POST /orders`. */
public record CreateOrderRequest(
        @NotNull @Min(1) Integer customerId,
        @NotNull @Size(min = 1, max = 10) @UniqueProductIds List<@NotNull @Valid OrderLine> items) {

    public record OrderLine(@NotNull @Min(1) Integer productId, @NotNull @Min(1) @Max(10) Integer quantity) {}

    /** `POST /secure/orders`: the customer comes from the token, so any `customer_id` is ignored. */
    public record Secure(@NotNull @Size(min = 1, max = 10) @UniqueProductIds List<@NotNull @Valid OrderLine> items) {}

    @Target({FIELD, METHOD, PARAMETER, RECORD_COMPONENT})
    @Retention(RetentionPolicy.RUNTIME)
    @Constraint(validatedBy = UniqueProductIdsValidator.class)
    public @interface UniqueProductIds {
        String message() default "duplicate product_id";
        Class<?>[] groups() default {};
        Class<? extends Payload>[] payload() default {};
    }

    public static class UniqueProductIdsValidator implements ConstraintValidator<UniqueProductIds, List<OrderLine>> {
        @Override
        public boolean isValid(List<OrderLine> lines, ConstraintValidatorContext context) {
            if (lines == null) return true;
            var seen = new HashSet<Integer>();
            for (OrderLine line : lines) {
                if (line != null && line.productId() != null && !seen.add(line.productId())) return false;
            }
            return true;
        }
    }
}
