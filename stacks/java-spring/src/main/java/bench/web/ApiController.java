package bench.web;

import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import bench.aggregate.AggregateService;
import bench.aggregate.AggregateService.AggregateResponse;
import bench.auth.CurrentCustomer;
import bench.catalog.CatalogService;
import bench.catalog.CatalogService.CategoryPage;
import bench.catalog.Product;
import bench.downstream.DownstreamService;
import bench.downstream.DownstreamService.Stock;
import bench.orders.CreateOrderRequest;
import bench.orders.OrdersService;
import bench.orders.OrdersService.CustomerOrders;
import bench.orders.OrdersService.OrderView;
import bench.transform.Cart;
import bench.transform.TransformService;
import bench.transform.TransformService.CartSummary;
import jakarta.validation.Valid;

/**
 * Endpoints served both unprotected (tests 2–8) and under `/secure` (test 9, guarded by
 * {@link bench.auth.SecurityConfig}). The two customer-specific order routes have their own
 * `/secure` variants that take the customer from the token.
 */
@RestController
public class ApiController {
    private final CatalogService catalog;
    private final OrdersService orders;
    private final DownstreamService downstream;
    private final TransformService transformer;
    private final AggregateService aggregator;

    public ApiController(CatalogService catalog, OrdersService orders, DownstreamService downstream,
                         TransformService transformer, AggregateService aggregator) {
        this.catalog = catalog;
        this.orders = orders;
        this.downstream = downstream;
        this.transformer = transformer;
        this.aggregator = aggregator;
    }

    @GetMapping("/health")
    String health() {
        return "ok";
    }

    @GetMapping({"/products/{id}", "/secure/products/{id}"})
    Product product(@PathVariable int id) {
        return catalog.getProduct(id);
    }

    @GetMapping({"/categories/{id}/products", "/secure/categories/{id}/products"})
    CategoryPage categoryProducts(@PathVariable int id, @RequestParam(defaultValue = "0") int after) {
        return catalog.categoryPage(id, after);
    }

    @GetMapping("/customers/{id}/orders")
    CustomerOrders customerOrders(@PathVariable int id) {
        return orders.customerOrders(id);
    }

    @GetMapping("/secure/customers/{id}/orders")
    CustomerOrders secureCustomerOrders(@PathVariable int id, @AuthenticationPrincipal Jwt jwt) {
        if (id != CurrentCustomer.id(jwt)) throw new ResponseStatusException(HttpStatus.FORBIDDEN);
        return orders.customerOrders(id);
    }

    @PostMapping("/orders")
    @ResponseStatus(HttpStatus.CREATED)
    OrderView createOrder(@Valid @RequestBody CreateOrderRequest body) {
        return orders.createOrder(body.customerId(), body.items());
    }

    @PostMapping("/secure/orders")
    @ResponseStatus(HttpStatus.CREATED)
    OrderView secureCreateOrder(@Valid @RequestBody CreateOrderRequest.Secure body, @AuthenticationPrincipal Jwt jwt) {
        return orders.createOrder(CurrentCustomer.id(jwt), body.items());
    }

    @GetMapping({"/downstream/{id}", "/secure/downstream/{id}"})
    Stock inventory(@PathVariable int id) {
        return downstream.inventory(id);
    }

    @PostMapping({"/transform", "/secure/transform"})
    CartSummary transform(@Valid @RequestBody Cart cart) {
        return transformer.transform(cart);
    }

    @GetMapping({"/aggregate", "/secure/aggregate"})
    AggregateResponse aggregate(@RequestParam int region, @RequestParam("min_amount") int minAmount) {
        return aggregator.aggregate(region, minAmount);
    }
}
