#!/bin/sh
set -e

echo "=== base 이미지 빌드 ==="
docker build -f docker/Dockerfile.base -t autocoin-base .

echo "=== 서비스 이미지 빌드 ==="
docker compose build

echo "=== 완료 ==="
docker images | grep -E "autocoin|ct-"
