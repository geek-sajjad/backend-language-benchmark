#!/bin/sh
# Test-only RS256 key pair for test 9 and the conformance auth checks.
# Not committed: run once per checkout. The public key is mounted into every stack.
set -eu
cd "$(dirname "$0")"
[ -f private.pem ] && { echo "keys already exist in $(pwd)"; exit 0; }
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out private.pem
openssl pkey -in private.pem -pubout -out public.pem
chmod 644 public.pem private.pem   # read by the stack container user
echo "wrote $(pwd)/private.pem and public.pem"
