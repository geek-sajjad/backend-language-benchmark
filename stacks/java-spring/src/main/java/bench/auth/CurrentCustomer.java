package bench.auth;

import org.springframework.security.oauth2.jwt.Jwt;

/** The authenticated customer: the token's `sub` claim, already checked by the decoder. */
public final class CurrentCustomer {
    private CurrentCustomer() {}

    public static int id(Jwt jwt) {
        return Integer.parseInt(jwt.getSubject());
    }

    static boolean isCustomerId(String sub) {
        if (sub == null) return false;
        try {
            return Integer.parseInt(sub) >= 1;
        } catch (NumberFormatException e) {
            return false;
        }
    }
}
