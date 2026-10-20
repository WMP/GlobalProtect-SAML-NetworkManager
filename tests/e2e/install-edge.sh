#!/bin/bash
# Installs Microsoft Edge (amd64 only) from the Microsoft apt repository and
# the managed policy that hides its first-run, sign-in and sync prompts.
#
# An optional extra CA (for a TLS-inspecting build proxy) is read from the
# BuildKit secret /run/secrets/extra_ca; it is used for these downloads only
# and is not installed into the image. https_proxy is honoured as usual.
set -euo pipefail

policy_src="${1:?usage: $0 <edge-policy.json>}"
cacert=()
apt_opts=()
if [ -s /run/secrets/extra_ca ]; then
    cacert=(--cacert /run/secrets/extra_ca)
    apt_opts=(-o Acquire::https::CaInfo=/run/secrets/extra_ca)
fi

install -d -m 755 /etc/apt/keyrings
curl -fsSL "${cacert[@]}" https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/edge stable main" \
    > /etc/apt/sources.list.d/microsoft-edge.list
apt-get update "${apt_opts[@]}"
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${apt_opts[@]}" microsoft-edge-stable
# The package adds its own source file; keep the image free of both
rm -f /etc/apt/sources.list.d/microsoft-edge.list /etc/apt/sources.list.d/microsoft-edge-stable.list

install -D -m 644 "$policy_src" /etc/opt/edge/policies/managed/demo.json
