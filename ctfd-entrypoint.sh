#!/bin/sh
echo "Waiting for MariaDB to be ready..."
until mysqladmin ping -h db -uctfd -pctfd --silent; do
  sleep 1
done
# Ensure SECRET_KEY is set (required for multiple workers)
if [ -z "$SECRET_KEY" ]; then
    echo "SECRET_KEY not found in environment, generating one..."
    export SECRET_KEY=$(head -c 32 /dev/urandom | base64)
fi

echo "MariaDB is up, starting CTFd..."
exec /entrypoint.sh
