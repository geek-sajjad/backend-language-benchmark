package bench.web;

/** Invalid input found after request validation (for example an unknown product id): a 422. */
public class ValidationFailedException extends RuntimeException {
    private final Object details;

    public ValidationFailedException(Object details) {
        super("validation failed", null, false, false);
        this.details = details;
    }

    public Object details() {
        return details;
    }
}
