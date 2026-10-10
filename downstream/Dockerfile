FROM golang:1.27.2-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /downstream .

FROM alpine:3
COPY --from=build /downstream /downstream
USER 65534
ENTRYPOINT ["/downstream"]
