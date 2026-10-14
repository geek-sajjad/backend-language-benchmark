package bench;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Startup order: JWT key, sales dataset and the DB pool are all created as singletons while the
 * context refreshes; Tomcat only opens :8080 (and so /health) after that.
 */
@SpringBootApplication
public class BenchApplication {
    public static void main(String[] args) {
        SpringApplication.run(BenchApplication.class, args);
    }
}
