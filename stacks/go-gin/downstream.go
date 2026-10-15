package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/gin-gonic/gin"
)

// newHTTPClient returns the shared keep-alive client for test 6. net/http keeps only 2 idle
// connections per host (and 100 in total) by default, so most connections to the one downstream
// host would be closed after each request; both limits are raised (docs/03).
func newHTTPClient() *http.Client {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConns = 1024
	t.MaxIdleConnsPerHost = 1024
	return &http.Client{Transport: t}
}

type inventoryJSON struct {
	Available int64 `json:"available"`
}

type stockJSON struct {
	ProductID int64  `json:"product_id"`
	Available int64  `json:"available"`
	Source    string `json:"source"`
}

// inventory is test 6: GET /downstream/{id}, proxied to the mock through the shared client.
func (a *app) inventory(c *gin.Context) {
	var uri struct {
		ID int64 `uri:"id"`
	}
	if err := c.ShouldBindUri(&uri); err != nil {
		badRequest(c, err)
		return
	}
	req, err := http.NewRequestWithContext(c, http.MethodGet, fmt.Sprintf("%s/inventory/%d", a.downstreamURL, uri.ID), nil)
	if err != nil {
		internalError(c, err)
		return
	}
	resp, err := a.http.Do(req)
	if err != nil {
		badGateway(c, err)
		return
	}
	// Read the body to EOF so the connection goes back to the pool.
	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		badGateway(c, err)
		return
	}
	if resp.StatusCode != http.StatusOK {
		badGateway(c, fmt.Errorf("status %d", resp.StatusCode))
		return
	}
	var inv inventoryJSON
	if err := json.Unmarshal(body, &inv); err != nil {
		badGateway(c, err)
		return
	}
	c.JSON(http.StatusOK, stockJSON{ProductID: uri.ID, Available: inv.Available, Source: "downstream"})
}
