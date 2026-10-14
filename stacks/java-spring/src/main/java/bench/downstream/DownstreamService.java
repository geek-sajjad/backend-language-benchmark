package bench.downstream;

import java.net.http.HttpClient;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;

@Service
public class DownstreamService {
    private static final Logger log = LoggerFactory.getLogger(DownstreamService.class);

    public record Stock(int productId, int available, String source) {}

    private record Inventory(int available) {}

    private final RestClient client;

    /**
     * Test 6: one shared RestClient over the JDK HttpClient, whose connection pool keeps
     * connections alive with no per-host limit. HTTP/1.1 is set because the JDK client otherwise
     * asks every plain-HTTP connection to upgrade to h2c.
     */
    public DownstreamService(RestClient.Builder builder,
                             @Value("${DOWNSTREAM_URL:http://127.0.0.1:9000}") String baseUrl) {
        HttpClient http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();
        this.client = builder.baseUrl(baseUrl.replaceAll("/+$", ""))
                .requestFactory(new JdkClientHttpRequestFactory(http))
                .build();
    }

    public Stock inventory(int id) {
        Inventory inventory;
        try {
            inventory = client.get().uri("/inventory/{id}", id).retrieve().body(Inventory.class);
        } catch (RestClientException e) {
            log.error("downstream call failed", e);
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY);
        }
        return new Stock(id, inventory.available(), "downstream");
    }
}
