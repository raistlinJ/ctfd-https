#!/bin/sh
echo "Waiting for MariaDB to be ready..."
until mysqladmin ping -h db -uctfd -pctfd --silent; do
  sleep 1
done
echo "MariaDB is up, starting CTFd..."
exec /entrypoint.sh
