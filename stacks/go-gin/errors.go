package main

import (
	"log"
	"net/http"

	"github.com/gin-gonic/gin"
)

// Error bodies from docs/02-test-spec.md; conformance checks only the status code.

func notFound(c *gin.Context) {
	c.AbortWithStatusJSON(http.StatusNotFound, gin.H{"error": "not_found"})
}

func unauthorized(c *gin.Context) {
	c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "unauthorized"})
}

func forbidden(c *gin.Context) {
	c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "forbidden"})
}

func badRequest(c *gin.Context, err error) {
	c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "bad_request", "details": err.Error()})
}

func validationFailed(c *gin.Context, details any) {
	if err, ok := details.(error); ok {
		details = err.Error()
	}
	c.AbortWithStatusJSON(http.StatusUnprocessableEntity, gin.H{"error": "validation_failed", "details": details})
}

func badGateway(c *gin.Context, err error) {
	log.Printf("error: downstream call failed: %v", err)
	c.AbortWithStatusJSON(http.StatusBadGateway, gin.H{"error": "downstream_failed"})
}

func internalError(c *gin.Context, err error) {
	log.Printf("error: %v", err)
	c.AbortWithStatusJSON(http.StatusInternalServerError, gin.H{"error": "internal"})
}
