FROM golang:1.26-alpine AS builder
RUN apk add --no-cache gcc musl-dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=1 go build -o /ldap-proxy .

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /ldap-proxy /ldap-proxy
VOLUME /data
EXPOSE 3389 8080
ENTRYPOINT ["/ldap-proxy"]
