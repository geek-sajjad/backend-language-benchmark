package main

import (
	"cmp"
	"net/http"
	"slices"
	"strings"

	"github.com/gin-gonic/gin"
)

type cart struct {
	CartID   string     `json:"cart_id"`
	Currency string     `json:"currency"`
	Items    []lineItem `json:"items"`
}

type lineItem struct {
	LineID   int64       `json:"line_id"`
	Product  cartProduct `json:"product"`
	Pricing  pricing     `json:"pricing"`
	Quantity int64       `json:"quantity"`
}

type cartProduct struct {
	ID         int64             `json:"id"`
	Name       string            `json:"name"`
	Category   string            `json:"category"`
	Attributes map[string]string `json:"attributes"`
}

type pricing struct {
	Unit      int64      `json:"unit"`
	Currency  string     `json:"currency"`
	Discounts []discount `json:"discounts"`
}

type discount struct {
	Kind  string `json:"kind"` // percent | fixed
	Value int64  `json:"value"`
}

type cartSummary struct {
	CartID     string            `json:"cart_id"`
	Currency   string            `json:"currency"`
	ItemCount  int               `json:"item_count"`
	TotalCents int64             `json:"total_cents"`
	Categories []categorySummary `json:"categories"`
}

type categorySummary struct {
	Category      string    `json:"category"`
	Count         int       `json:"count"`
	SubtotalCents int64     `json:"subtotal_cents"`
	TopItems      []topItem `json:"top_items"`
}

type topItem struct {
	LineID     int64  `json:"line_id"`
	ProductID  int64  `json:"product_id"`
	Name       string `json:"name"`
	TotalCents int64  `json:"total_cents"`
}

// transform is test 7: POST /transform
func (a *app) transform(c *gin.Context) {
	var in cart
	if err := c.ShouldBindJSON(&in); err != nil {
		validationFailed(c, err)
		return
	}

	var total int64
	groups := make(map[string]*categorySummary)
	for _, item := range in.Items {
		lineTotal := discountedUnit(item.Pricing) * item.Quantity
		total += lineTotal
		g, ok := groups[item.Product.Category]
		if !ok {
			g = &categorySummary{Category: item.Product.Category}
			groups[item.Product.Category] = g
		}
		g.Count++
		g.SubtotalCents += lineTotal
		g.TopItems = append(g.TopItems, topItem{
			LineID:     item.LineID,
			ProductID:  item.Product.ID,
			Name:       normalizeName(item.Product.Name),
			TotalCents: lineTotal,
		})
	}

	categories := make([]categorySummary, 0, len(groups))
	for _, g := range groups {
		slices.SortFunc(g.TopItems, func(x, y topItem) int {
			return cmp.Or(cmp.Compare(y.TotalCents, x.TotalCents), cmp.Compare(x.LineID, y.LineID))
		})
		g.TopItems = g.TopItems[:min(3, len(g.TopItems))]
		categories = append(categories, *g)
	}
	slices.SortFunc(categories, func(x, y categorySummary) int {
		return cmp.Or(cmp.Compare(y.SubtotalCents, x.SubtotalCents), strings.Compare(x.Category, y.Category))
	})

	c.JSON(http.StatusOK, cartSummary{
		CartID:     in.CartID,
		Currency:   in.Currency,
		ItemCount:  len(in.Items),
		TotalCents: total,
		Categories: categories,
	})
}

// discountedUnit applies the discounts to the unit price in order; integer cents, rounding toward zero.
func discountedUnit(p pricing) int64 {
	unit := p.Unit
	for _, d := range p.Discounts {
		switch d.Kind {
		case "percent":
			unit -= unit * d.Value / 100
		case "fixed":
			unit = max(0, unit-d.Value)
		}
	}
	return unit
}

// normalizeName trims, lowercases and collapses whitespace runs to a single space.
func normalizeName(name string) string {
	return strings.ToLower(strings.Join(strings.Fields(name), " "))
}
