// Downstream mock for test 6: GET /inventory/{id} answers after a fixed delay.
// The response is a pure function of id, so conformance can predict it.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

type inventory struct {
	ID        int64  `json:"id"`
	Warehouse string `json:"warehouse"`
	Available int64  `json:"available"`
}

func main() {
	addr := envOr("LISTEN_ADDR", ":9000")
	delayMs, err := strconv.Atoi(envOr("DELAY_MS", "50"))
	if err != nil {
		log.Fatalf("DELAY_MS: %v", err)
	}
	delay := time.Duration(delayMs) * time.Millisecond

	mux := http.NewServeMux()
	mux.HandleFunc("GET /inventory/{id}", func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil || id < 1 {
			http.Error(w, `{"error":"bad id"}`, http.StatusBadRequest)
			return
		}
		time.Sleep(delay)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(inventory{
			ID:        id,
			Warehouse: "wh-" + strconv.FormatInt(id%8, 10),
			Available: (id * 7919) % 1000,
		})
	})
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       5 * time.Minute,
	}
	log.Printf("downstream mock on %s, delay %v", addr, delay)
	log.Fatal(srv.ListenAndServe())
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
