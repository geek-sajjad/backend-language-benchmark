package main

import (
	"context"
	"crypto/rsa"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type app struct {
	db            *gorm.DB
	http          *http.Client
	downstreamURL string
	sales         []Sale
	jwtKey        *rsa.PublicKey
}

func main() {
	jwtKey := loadPublicKey(envOr("JWT_PUBLIC_KEY_FILE", "/run/secrets/jwt_public.pem"))
	sales := buildSales()
	db := connectDB()

	a := &app{
		db:            db,
		http:          newHTTPClient(),
		downstreamURL: strings.TrimRight(envOr("DOWNSTREAM_URL", "http://127.0.0.1:9000"), "/"),
		sales:         sales,
		jwtKey:        jwtKey,
	}

	// gin.New instead of gin.Default: Default adds the per-request access logger.
	r := gin.New()
	r.Use(gin.Recovery())
	r.GET("/health", func(c *gin.Context) { c.String(http.StatusOK, "ok") })

	public := r.Group("")
	a.apiRoutes(public)
	public.GET("/customers/:id/orders", a.customerOrders)
	public.POST("/orders", a.createOrder)

	secure := r.Group("/secure", a.requireAuth)
	a.apiRoutes(secure)
	secure.GET("/customers/:id/orders", a.secureCustomerOrders)
	secure.POST("/orders", a.secureCreateOrder)

	srv := &http.Server{Addr: ":8080", Handler: r}
	go func() {
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("server error: %v", err)
		}
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdown)
}

// apiRoutes are served both unprotected (tests 2–8) and under /secure (test 9).
func (a *app) apiRoutes(g *gin.RouterGroup) {
	g.GET("/products/:id", a.getProduct)
	g.GET("/categories/:id/products", a.categoryProducts)
	g.GET("/downstream/:id", a.inventory)
	g.POST("/transform", a.transform)
	g.GET("/aggregate", a.aggregate)
}

func connectDB() *gorm.DB {
	dsn := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable",
		envOr("DB_HOST", "127.0.0.1"), envOr("DB_PORT", "5432"), envOr("DB_USER", "bench"),
		envOr("DB_PASSWORD", "bench"), envOr("DB_NAME", "bench"))
	poolSize, err := strconv.Atoi(envOr("DB_POOL_SIZE", "64"))
	if err != nil {
		log.Fatalf("DB_POOL_SIZE must be a number: %v", err)
	}
	config := &gorm.Config{
		// Errors only: no SQL logging, and no slow-query lines (they would log every query under overload).
		Logger: logger.New(log.New(os.Stderr, "", log.LstdFlags), logger.Config{
			LogLevel:                  logger.Warn,
			SlowThreshold:             0,
			IgnoreRecordNotFoundError: true,
		}),
		// Maps driver errors to gorm.ErrForeignKeyViolated and friends.
		TranslateError: true,
		// Postgres stores microseconds; truncating keeps the 201 response equal to the stored row.
		NowFunc: func() time.Time { return time.Now().UTC().Truncate(time.Microsecond) },
	}

	// The DB may still be starting (compose up, DB reset): retry for up to a minute.
	for attempt := 1; ; attempt++ {
		db, err := gorm.Open(postgres.Open(dsn), config)
		if err == nil {
			sqlDB, _ := db.DB()
			sqlDB.SetMaxOpenConns(poolSize)
			// database/sql keeps only 2 idle connections by default and would reconnect under load.
			sqlDB.SetMaxIdleConns(poolSize)
			return db
		}
		if attempt == 60 {
			log.Fatalf("database not reachable: %v", err)
		}
		log.Printf("warn: database not reachable yet (%v), retrying", err)
		time.Sleep(time.Second)
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
