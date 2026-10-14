package bench.web;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.hibernate.exception.ConstraintViolationException;
import org.hibernate.exception.ConstraintViolationException.ConstraintKind;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.TypeMismatchException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;

/** Error bodies from docs/02-test-spec.md: `{"error": …}`, plus `details` on a 422. */
@RestControllerAdvice
public class ErrorHandler {
    private static final Logger log = LoggerFactory.getLogger(ErrorHandler.class);

    private static final Map<Integer, String> ERROR_CODES = Map.of(
            400, "bad_request",
            401, "unauthorized",
            403, "forbidden",
            404, "not_found",
            405, "method_not_allowed",
            422, "validation_failed",
            502, "downstream_failed");

    @ExceptionHandler
    ResponseEntity<Map<String, Object>> invalidBody(MethodArgumentNotValidException e) {
        Map<String, Object> details = new LinkedHashMap<>();
        e.getFieldErrors().forEach(f -> details.put(f.getField(), f.getDefaultMessage()));
        return validationFailed(details);
    }

    @ExceptionHandler
    ResponseEntity<Map<String, Object>> invalidArguments(HandlerMethodValidationException e) {
        List<String> details = e.getAllErrors().stream().map(err -> String.valueOf(err.getDefaultMessage())).toList();
        return validationFailed(details);
    }

    /** Malformed JSON, or a value of the wrong JSON type: a 422 like any other invalid input. */
    @ExceptionHandler
    ResponseEntity<Map<String, Object>> unreadableBody(HttpMessageNotReadableException e) {
        return validationFailed("malformed JSON or a value of the wrong type");
    }

    /** A malformed path or query parameter, for example a non-numeric id. */
    @ExceptionHandler
    ResponseEntity<Map<String, Object>> badParameter(TypeMismatchException e) {
        return ResponseEntity.badRequest().body(Map.of("error", "bad_request"));
    }

    @ExceptionHandler
    ResponseEntity<Map<String, Object>> invalidData(ValidationFailedException e) {
        return validationFailed(e.details());
    }

    /** Test 5: an unknown customer fails the order insert on the foreign key. */
    @ExceptionHandler
    ResponseEntity<Map<String, Object>> integrity(DataIntegrityViolationException e) {
        if (e.getCause() instanceof ConstraintViolationException c && c.getKind() == ConstraintKind.FOREIGN_KEY) {
            return validationFailed(Map.of("customer_id", "unknown customer"));
        }
        return other(e);
    }

    @ExceptionHandler
    ResponseEntity<Map<String, Object>> other(Exception e) {
        int status = e instanceof ErrorResponse r ? r.getStatusCode().value() : 500;
        if (status >= 500 && status != 502) log.error("request failed", e);
        return ResponseEntity.status(status).body(Map.of("error", ERROR_CODES.getOrDefault(status, "internal")));
    }

    private static ResponseEntity<Map<String, Object>> validationFailed(Object details) {
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_CONTENT)
                .body(Map.of("error", "validation_failed", "details", details));
    }
}
