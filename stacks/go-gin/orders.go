package main

import (
	"cmp"
	"errors"
	"net/http"
	"slices"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

type Order struct {
	ID         int64
	CustomerID int32
	Status     string
	TotalCents int32
	CreatedAt  time.Time // set by GORM from NowFunc on Create
	Items      []OrderItem
}

type OrderItem struct {
	ID             int64
	OrderID        int64
	ProductID      int32
	Quantity       int32
	UnitPriceCents int32
	Product        Product
}

type orderItemJSON struct {
	ProductID      int32  `json:"product_id"`
	Name           string `json:"name"`
	UnitPriceCents int32  `json:"unit_price_cents"`
	Quantity       int32  `json:"quantity"`
}

type orderJSON struct {
	ID         int64           `json:"id"`
	Status     string          `json:"status"`
	TotalCents int32           `json:"total_cents"`
	CreatedAt  time.Time       `json:"created_at"`
	Items      []orderItemJSON `json:"items"`
}

type customerOrdersJSON struct {
	CustomerID int32       `json:"customer_id"`
	Orders     []orderJSON `json:"orders"`
}

func toOrderJSON(o Order) orderJSON {
	items := make([]orderItemJSON, len(o.Items))
	for i, it := range o.Items {
		items[i] = orderItemJSON{ProductID: it.ProductID, Name: it.Product.Name, UnitPriceCents: it.UnitPriceCents, Quantity: it.Quantity}
	}
	return orderJSON{ID: o.ID, Status: o.Status, TotalCents: o.TotalCents, CreatedAt: o.CreatedAt, Items: items}
}

// customerOrders is test 4: GET /customers/{id}/orders. 2 queries: the 10 orders, then their
// items joined with products (Preload with a belongs-to Joins).
func (a *app) customerOrders(c *gin.Context) {
	var uri idURI
	if err := c.ShouldBindUri(&uri); err != nil {
		badRequest(c, err)
		return
	}
	a.writeCustomerOrders(c, uri.ID)
}

// secureCustomerOrders: only the token's own customer may read their orders.
func (a *app) secureCustomerOrders(c *gin.Context) {
	var uri idURI
	if err := c.ShouldBindUri(&uri); err != nil {
		badRequest(c, err)
		return
	}
	if uri.ID != authCustomer(c) {
		forbidden(c)
		return
	}
	a.writeCustomerOrders(c, uri.ID)
}

func (a *app) writeCustomerOrders(c *gin.Context, customerID int32) {
	var orders []Order
	err := a.db.WithContext(c).
		Preload("Items", func(tx *gorm.DB) *gorm.DB {
			// Only the product's id and name are needed for the item.
			return tx.Joins("Product", a.db.Select("id", "name")).Order("order_items.id")
		}).
		Where("customer_id = ?", customerID).
		Order("created_at DESC, id DESC").Limit(10).
		Find(&orders).Error
	if err != nil {
		internalError(c, err)
		return
	}

	resp := customerOrdersJSON{CustomerID: customerID, Orders: make([]orderJSON, len(orders))}
	for i, o := range orders {
		resp.Orders[i] = toOrderJSON(o)
	}
	c.JSON(http.StatusOK, resp)
}

type orderLine struct {
	ProductID int32 `json:"product_id" binding:"required,min=1"`
	Quantity  int32 `json:"quantity" binding:"required,min=1,max=10"`
}

type createOrderRequest struct {
	CustomerID int32       `json:"customer_id" binding:"required,min=1"`
	Items      []orderLine `json:"items" binding:"required,min=1,max=10,unique=ProductID,dive"`
}

// secureCreateOrderRequest: on /secure/orders any customer_id in the body is ignored; the token's sub is used.
type secureCreateOrderRequest struct {
	Items []orderLine `json:"items" binding:"required,min=1,max=10,unique=ProductID,dive"`
}

// createOrder is test 5: POST /orders
func (a *app) createOrder(c *gin.Context) {
	var req createOrderRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		validationFailed(c, err)
		return
	}
	a.insertOrder(c, req.CustomerID, req.Items)
}

func (a *app) secureCreateOrder(c *gin.Context) {
	var req secureCreateOrderRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		validationFailed(c, err)
		return
	}
	a.insertOrder(c, authCustomer(c), req.Items)
}

var errUnknownProduct = errors.New("unknown product_id")

// insertOrder runs one transaction: read prices, insert the order and its items, decrement stock
// atomically. Items are handled in ascending product_id order so concurrent orders lock rows in the
// same order.
func (a *app) insertOrder(c *gin.Context, customerID int32, lines []orderLine) {
	slices.SortFunc(lines, func(x, y orderLine) int { return cmp.Compare(x.ProductID, y.ProductID) })
	ids := make([]int32, len(lines))
	for i, l := range lines {
		ids[i] = l.ProductID
	}

	var order Order
	err := a.db.WithContext(c).Transaction(func(tx *gorm.DB) error {
		var products []Product
		if err := tx.Select("id", "name", "price_cents").Where("id IN ?", ids).Order("id").Find(&products).Error; err != nil {
			return err
		}
		if len(products) != len(lines) {
			return errUnknownProduct
		}

		order = Order{CustomerID: customerID, Status: "pending"}
		
		items := make([]OrderItem, len(lines))
		for i, l := range lines {
			items[i] = OrderItem{ProductID: l.ProductID, Quantity: l.Quantity, UnitPriceCents: products[i].PriceCents}
			order.TotalCents += products[i].PriceCents * l.Quantity
		}
		if err := tx.Create(&order).Error; err != nil {
			return err
		}
		for i := range items {
			items[i].OrderID = order.ID
		}
		if err := tx.Omit(clause.Associations).Create(&items).Error; err != nil {
			return err
		}
		for _, l := range lines {
			err := tx.Model(&Product{}).Where("id = ?", l.ProductID).
				Update("stock", gorm.Expr("stock - ?", l.Quantity)).Error
			if err != nil {
				return err
			}
		}

		for i := range items {
			items[i].Product = products[i]
		}
		order.Items = items
		return nil
	})
	switch {
	case errors.Is(err, errUnknownProduct):
		validationFailed(c, gin.H{"items": "unknown product_id"})
	case errors.Is(err, gorm.ErrForeignKeyViolated):
		validationFailed(c, gin.H{"customer_id": "unknown customer"})
	case err != nil:
		internalError(c, err)
	default:
		c.JSON(http.StatusCreated, toOrderJSON(order))
	}
}
