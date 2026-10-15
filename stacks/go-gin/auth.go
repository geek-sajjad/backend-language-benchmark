package main

import (
	"crypto/rsa"
	"log"
	"os"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

const customerKey = "customer_id"

// loadPublicKey reads the PEM once at startup; no JWKS fetching (docs/02-test-spec.md, Authentication).
func loadPublicKey(path string) *rsa.PublicKey {
	pem, err := os.ReadFile(path)
	if err != nil {
		log.Fatalf("cannot read JWT public key %s: %v", path, err)
	}
	key, err := jwt.ParseRSAPublicKeyFromPEM(pem)
	if err != nil {
		log.Fatalf("JWT public key is not an RSA PEM: %v", err)
	}
	return key
}

var jwtParser = jwt.NewParser(
	jwt.WithValidMethods([]string{"RS256"}),
	jwt.WithIssuer("bench-idp"),
	jwt.WithAudience("bench-api"),
	jwt.WithExpirationRequired(),
)

// requireAuth guards the /secure group: RS256 signature, exp, iss and aud, and a numeric sub,
// which handlers read with authCustomer.
func (a *app) requireAuth(c *gin.Context) {
	token, ok := strings.CutPrefix(c.GetHeader("Authorization"), "Bearer ")
	if !ok {
		unauthorized(c)
		return
	}
	var claims jwt.RegisteredClaims
	_, err := jwtParser.ParseWithClaims(token, &claims, func(*jwt.Token) (any, error) { return a.jwtKey, nil })
	if err != nil {
		unauthorized(c)
		return
	}
	customerID, err := strconv.ParseInt(claims.Subject, 10, 32)
	if err != nil {
		unauthorized(c)
		return
	}
	c.Set(customerKey, int32(customerID))
	c.Next()
}

// authCustomer is the customer id from the token's sub claim.
func authCustomer(c *gin.Context) int32 {
	return c.MustGet(customerKey).(int32)
}
