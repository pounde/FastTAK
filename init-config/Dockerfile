FROM alpine:3.20

RUN apk add --no-cache bash openjdk17-jre-headless openssl

COPY start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]
