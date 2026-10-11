#!/bin/sh
set -eu
cd /var/www/html
attempt=0
until [ -f wp-config.php ] && php /opt/commerce/database-ready.php 2>/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 90 ]; then echo 'WordPress database did not become ready' >&2; exit 1; fi
  sleep 2
done
if ! wp core is-installed --quiet; then
  wp core install --url="$STORE_PUBLIC_URL" --title="$WP_TITLE" \
    --admin_user="$WP_ADMIN_USER" --admin_password="$WP_ADMIN_PASSWORD" \
    --admin_email="$WP_ADMIN_EMAIL" --skip-email
fi
wp core update-db
wp plugin activate woocommerce
wp theme activate storefront
wp rewrite structure '/%postname%/' --hard
wp eval-file /opt/commerce/seed.php
echo 'WooCommerce bootstrap complete'
