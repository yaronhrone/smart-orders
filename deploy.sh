#!/bin/bash
set -e

cd ~/smart-orders
git pull
docker-compose -f docker-compose.prod.yml up -d --build web frontend
echo "Deploy done!"
