package main

import (
	"errors"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

// Product is the full products row (test 3 requires full entities).
type Product struct {
	ID          int32     `json:"id"`
	SKU         string    `json:"sku"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	PriceCents  int32     `json:"price_cents"`
	Stock       int32     `json:"stock"`
	CategoryID  int32     `json:"category_id"`
	CreatedAt   time.Time `json:"created_at"`
}

type idURI struct {
	ID int32 `uri:"id"`
}

// getProduct is test 2: GET /products/{id}
func (a *app) getProduct(c *gin.Context) {
	var uri idURI
	if err := c.ShouldBindUri(&uri); err != nil {
		badRequest(c, err)
		return
	}
	var p Product
	if err := a.db.WithContext(c).First(&p, uri.ID).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			notFound(c)
		} else {
			internalError(c, err)
		}
		return
	}
	c.JSON(http.StatusOK, p)
}

type pageQuery struct {
	After int32 `form:"after"`
}

type productSummary struct {
	ID         int32     `json:"id"`
	SKU        string    `json:"sku"`
	Name       string    `json:"name"`
	PriceCents int32     `json:"price_cents"`
	Stock      int32     `json:"stock"`
	CreatedAt  time.Time `json:"created_at"`
}

type categoryPage struct {
	CategoryID int32            `json:"category_id"`
	NextAfter  *int32           `json:"next_after"`
	Items      []productSummary `json:"items"`
}

// categoryProducts is test 3: GET /categories/{id}/products?after={after_id}
func (a *app) categoryProducts(c *gin.Context) {
	var uri idURI
	if err := c.ShouldBindUri(&uri); err != nil {
		badRequest(c, err)
		return
	}
	var page pageQuery
	if err := c.ShouldBindQuery(&page); err != nil {
		badRequest(c, err)
		return
	}
	var products []Product
	err := a.db.WithContext(c).
		Where("category_id = ? AND id > ?", uri.ID, page.After).
		Order("id").Limit(50).
		Find(&products).Error
	if err != nil {
		internalError(c, err)
		return
	}

	items := make([]productSummary, len(products))
	for i, p := range products {
		items[i] = productSummary{ID: p.ID, SKU: p.SKU, Name: p.Name, PriceCents: p.PriceCents, Stock: p.Stock, CreatedAt: p.CreatedAt}
	}
	resp := categoryPage{CategoryID: uri.ID, Items: items}
	if len(items) > 0 {
		resp.NextAfter = &items[len(items)-1].ID
	}
	c.JSON(http.StatusOK, resp)
}
