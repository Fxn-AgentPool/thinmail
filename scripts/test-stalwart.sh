#!/bin/sh
# Throwaway Stalwart (Docker) with account alice@test.lan / alice-test-pw-123,
# for trying thinmail for real. Ports on 127.0.0.1: IMAPS 10993, SMTP 10025.
#
#   scripts/test-stalwart.sh up       # create (or recreate) it
#   scripts/test-stalwart.sh down     # remove container and data
#
# Needs: docker (via sudo), stalwart-cli (STALWART_CLI=path; release assets of
# github.com/stalwartlabs/cli), openssl. Data in $DIR (default /data/lab/mail-thin/stalwart).
set -eu
DIR=${DIR:-/data/lab/mail-thin/stalwart}
CLI=${STALWART_CLI:-/data/lab/mail-thin/cli-dl/stalwart-cli-x86_64-unknown-linux-musl/stalwart-cli}
IMAGE=stalwartlabs/stalwart:latest
NAME=tm-stalwart
PORTS="-p 127.0.0.1:18080:8080 -p 127.0.0.1:10993:993 -p 127.0.0.1:10025:25"

down() { sudo docker rm -f $NAME >/dev/null 2>&1 || true; sudo rm -rf "$DIR"; }

case "${1:-}" in
down) down ;;
up)
    down
    mkdir -p "$DIR/etc" "$DIR/data"
    # Stalwart >= 0.16 / 1.x: config.json only names the data store; the rest
    # lives in the store and is set through the management API (stalwart-cli).
    echo '{"@type":"RocksDb","path":"/var/lib/stalwart/"}' > "$DIR/etc/config.json"
    sudo chown -R 2000:2000 "$DIR"
    PW=$(openssl rand -hex 12)
    PLAN=$(mktemp)
    # 1. recovery mode: management API only, fixed admin credential
    sudo docker run -d --name $NAME -m 400m -e STALWART_RECOVERY_MODE=1 -e "STALWART_RECOVERY_ADMIN=admin:$PW" \
        $PORTS -v "$DIR/etc:/etc/stalwart" -v "$DIR/data:/var/lib/stalwart" $IMAGE >/dev/null
    for _ in $(seq 30); do curl -s -o /dev/null http://127.0.0.1:18080/ && break; sleep 1; done
    cli() { "$CLI" --url http://127.0.0.1:18080 --user admin --password "$PW" "$@"; }
    printf '%s\n' \
      '{"@type":"upsert","object":"Domain","matchOn":["name"],"value":{"d":{"name":"test.lan","certificateManagement":{"@type":"Manual"},"dkimManagement":{"@type":"Automatic"},"dnsManagement":{"@type":"Manual"},"subAddressing":{"@type":"Enabled"}}}}' \
      '{"@type":"update","object":"SystemSettings","value":{"defaultHostname":"mail.test.lan","defaultDomainId":"#d"}}' \
      > "$PLAN"
    cli apply --file "$PLAN"
    rm -f "$PLAN"
    DOMAIN_ID=$(cli query Domain | awk '$2 == "test.lan" {print $1}')
    # credentials is a map of id -> Credential (a JSON list is refused)
    cli create Account/User --field name=alice --field "domainId=$DOMAIN_ID" \
        --field 'credentials={"0":{"@type":"Password","secret":"alice-test-pw-123"}}'
    # 2. normal mode: mail listeners with the default (self-signed) certificate
    sudo docker rm -f $NAME >/dev/null
    sudo docker run -d --name $NAME -m 400m $PORTS \
        -v "$DIR/etc:/etc/stalwart" -v "$DIR/data:/var/lib/stalwart" $IMAGE >/dev/null
    # Docker's port proxy accepts before Stalwart listens: wait for real greetings.
    for _ in $(seq 60); do
        python3 -c "import smtplib, imaplib, ssl
smtplib.SMTP('127.0.0.1', 10025, timeout=3).quit()
c = ssl.create_default_context(); c.check_hostname = False; c.verify_mode = ssl.CERT_NONE
imaplib.IMAP4_SSL('127.0.0.1', 10993, ssl_context=c, timeout=3).logout()" 2>/dev/null && break
        sleep 1
    done
    echo "ready: IMAPS 127.0.0.1:10993, SMTP 127.0.0.1:10025, alice@test.lan / alice-test-pw-123"
    ;;
*) echo "usage: $0 up|down" >&2; exit 2 ;;
esac
