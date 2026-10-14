package bench.aggregate;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Service;

@Service
public class AggregateService {
    private static final long SALES_SEED = 0x5EED_BE7C_0001L;
    private static final int SALES_COUNT = 50_000;

    private static final Comparator<GroupStats> BY_SUM =
            Comparator.comparingLong(GroupStats::sumAmount).reversed().thenComparingInt(GroupStats::category);

    public record Sale(int id, int region, int category, int customerTier, int amountCents, int quantity, int day) {}

    public record GroupStats(int category, int count, long sumAmount, int minAmount, int maxAmount, long avgAmount,
                             long totalQuantity) {}

    public record AggregateResponse(int region, int minAmount, List<GroupStats> groups) {}

    /** Per-category accumulator for one request. */
    private static final class Group {
        int count;
        long sum;
        int min = Integer.MAX_VALUE;
        int max;
        long quantity;
    }

    // Built when the bean is created, before Tomcat starts listening.
    private final Sale[] sales = buildSales();

    /** Test 8: GET /aggregate?region={r}&min_amount={m} */
    public AggregateResponse aggregate(int region, int minAmount) {
        Map<Integer, Group> groups = new HashMap<>();
        for (Sale sale : sales) {
            if (sale.region() != region || sale.amountCents() < minAmount) continue;
            Group g = groups.computeIfAbsent(sale.category(), k -> new Group());
            g.count++;
            g.sum += sale.amountCents();
            g.min = Math.min(g.min, sale.amountCents());
            g.max = Math.max(g.max, sale.amountCents());
            g.quantity += sale.quantity();
        }

        List<GroupStats> stats = new ArrayList<>(groups.size());
        for (Map.Entry<Integer, Group> e : groups.entrySet()) {
            Group g = e.getValue();
            stats.add(new GroupStats(e.getKey(), g.count, g.sum, g.min, g.max, g.sum / g.count, g.quantity));
        }
        stats.sort(BY_SUM);
        return new AggregateResponse(region, minAmount, stats.subList(0, Math.min(10, stats.size())));
    }

    /** The test 8 dataset; fields are drawn in declaration order (conformance/reference/reference.py). */
    static Sale[] buildSales() {
        var rng = new SplitMix64(SALES_SEED);
        Sale[] sales = new Sale[SALES_COUNT];
        for (int id = 1; id <= SALES_COUNT; id++) {
            int region = rng.below(8);
            int category = rng.below(50);
            int tier = rng.below(4);
            int amount = 100 + rng.below(100_000);
            int quantity = 1 + rng.below(10);
            int day = rng.below(365);
            sales[id - 1] = new Sale(id, region, category, tier, amount, quantity, day);
        }
        return sales;
    }

    /** splitmix64 on a 64-bit long; values are reduced as unsigned. */
    private static final class SplitMix64 {
        private long state;

        SplitMix64(long seed) {
            this.state = seed;
        }

        long next() {
            state += 0x9E37_79B9_7F4A_7C15L;
            long z = state;
            z = (z ^ (z >>> 30)) * 0xBF58_476D_1CE4_E5B9L;
            z = (z ^ (z >>> 27)) * 0x94D0_49BB_1331_11EBL;
            return z ^ (z >>> 31);
        }

        int below(int n) {
            return (int) Long.remainderUnsigned(next(), n);
        }
    }
}
