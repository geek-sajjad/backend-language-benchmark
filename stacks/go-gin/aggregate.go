package main

import (
	"cmp"
	"math"
	"net/http"
	"slices"

	"github.com/gin-gonic/gin"
)

const (
	salesSeed  = 0x5EED_BE7C_0001
	salesCount = 50_000
)

type Sale struct {
	ID           uint32
	Region       uint8
	Category     uint8
	CustomerTier uint8
	AmountCents  uint32
	Quantity     uint32
	Day          uint16
}

type splitMix64 uint64

func (s *splitMix64) next() uint64 {
	*s += 0x9E37_79B9_7F4A_7C15
	z := uint64(*s)
	z = (z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9
	z = (z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB
	return z ^ (z >> 31)
}

func (s *splitMix64) below(n uint64) uint64 { return s.next() % n }

// buildSales builds the test 8 dataset; fields are drawn in declaration order
// (conformance/reference/reference.py).
func buildSales() []Sale {
	rng := splitMix64(salesSeed)
	sales := make([]Sale, salesCount)
	for i := range sales {
		sales[i] = Sale{
			ID:           uint32(i + 1),
			Region:       uint8(rng.below(8)),
			Category:     uint8(rng.below(50)),
			CustomerTier: uint8(rng.below(4)),
			AmountCents:  100 + uint32(rng.below(100_000)),
			Quantity:     1 + uint32(rng.below(10)),
			Day:          uint16(rng.below(365)),
		}
	}
	return sales
}

type aggregateQuery struct {
	Region    uint8  `form:"region"`
	MinAmount uint32 `form:"min_amount"`
}

type salesGroup struct {
	Category      uint8  `json:"category"`
	Count         uint64 `json:"count"`
	SumAmount     uint64 `json:"sum_amount"`
	MinAmount     uint32 `json:"min_amount"`
	MaxAmount     uint32 `json:"max_amount"`
	AvgAmount     uint64 `json:"avg_amount"`
	TotalQuantity uint64 `json:"total_quantity"`
}

type aggregateJSON struct {
	Region    uint8        `json:"region"`
	MinAmount uint32       `json:"min_amount"`
	Groups    []salesGroup `json:"groups"`
}

// aggregate is test 8: GET /aggregate?region={r}&min_amount={m}
func (a *app) aggregate(c *gin.Context) {
	var q aggregateQuery
	if err := c.ShouldBindQuery(&q); err != nil {
		badRequest(c, err)
		return
	}

	groups := make(map[uint8]*salesGroup)
	for i := range a.sales {
		s := &a.sales[i]
		if s.Region != q.Region || s.AmountCents < q.MinAmount {
			continue
		}
		g, ok := groups[s.Category]
		if !ok {
			g = &salesGroup{Category: s.Category, MinAmount: math.MaxUint32}
			groups[s.Category] = g
		}
		g.Count++
		g.SumAmount += uint64(s.AmountCents)
		g.MinAmount = min(g.MinAmount, s.AmountCents)
		g.MaxAmount = max(g.MaxAmount, s.AmountCents)
		g.TotalQuantity += uint64(s.Quantity)
	}

	result := make([]salesGroup, 0, len(groups))
	for _, g := range groups {
		g.AvgAmount = g.SumAmount / g.Count
		result = append(result, *g)
	}
	slices.SortFunc(result, func(x, y salesGroup) int {
		return cmp.Or(cmp.Compare(y.SumAmount, x.SumAmount), cmp.Compare(x.Category, y.Category))
	})
	result = result[:min(10, len(result))]

	c.JSON(http.StatusOK, aggregateJSON{Region: q.Region, MinAmount: q.MinAmount, Groups: result})
}
