#!/bin/sh
set -e

: "${WG_LISTEN_PORT:=51820}"
: "${WG_ADDRESS:?WG_ADDRESS required}"
: "${WG_SERVER_PRIVKEY:?WG_SERVER_PRIVKEY required}"
: "${WG_PEER_PUBKEY:?WG_PEER_PUBKEY required}"
: "${WG_PEER_IP:?WG_PEER_IP required}"

mkdir -p /etc/wireguard
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = ${WG_ADDRESS}
ListenPort = ${WG_LISTEN_PORT}
PrivateKey = ${WG_SERVER_PRIVKEY}
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
PublicKey = ${WG_PEER_PUBKEY}
AllowedIPs = ${WG_PEER_IP}
EOF
chmod 600 /etc/wireguard/wg0.conf

term() { wg-quick down wg0 2>/dev/null || true; exit 0; }
trap term TERM INT

wg-quick up wg0
echo "── wireguard up ──"
wg show || true

# Stay alive but interruptible so the trap fires on `docker stop`.
while true; do
  sleep 3600 &
  wait $!
done
