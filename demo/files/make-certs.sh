#!/bin/bash
# Creates the demo CA ("Contoso Demo Root CA", 10 years) and a server
# certificate for vpn.contoso.example / login.contoso.example signed by it.
#
# Usage: make-certs.sh <output-dir>
# Output: ca.crt, ca.key, server.crt, server.key (and server.csr/ext files
# removed). The Dockerfile imports ca.crt into the system store and the NSS db.
set -euo pipefail

out="${1:?usage: $0 <output-dir>}"
mkdir -p "$out"
cd "$out"

openssl genrsa -out ca.key 3072 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
    -subj "/O=Contoso Demo/CN=Contoso Demo Root CA" -out ca.crt \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

openssl genrsa -out server.key 2048 2>/dev/null
openssl req -new -key server.key -subj "/O=Contoso Demo/CN=vpn.contoso.example" -out server.csr
cat > server.ext <<EXT
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:vpn.contoso.example,DNS:login.contoso.example
EXT
# The 398-day limit only applies to publicly trusted CAs, not to a local root
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days 3650 -sha256 -extfile server.ext 2>/dev/null

rm -f server.csr server.ext ca.srl
chmod 644 ca.crt server.crt
chmod 600 ca.key server.key
echo "Created $out/ca.crt and $out/server.{crt,key}"
