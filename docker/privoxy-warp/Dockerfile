FROM alpine:3.20

RUN apk add --no-cache privoxy
COPY config /etc/privoxy/config

EXPOSE 8118
CMD ["privoxy", "--no-daemon", "/etc/privoxy/config"]
