#!/bin/sh
set -eu
cp -r /usr/src/wordpress/. /var/www/html/
# This packaged plugin was renamed. Loading both versions would duplicate hooks.
rm -f /var/www/html/wp-content/mu-plugins/commerce-theme.php
for asset in everyday-tote.svg daily-bottle.svg notes-notebook.svg; do
  rm -f "/var/www/html/wp-content/commerce-assets/$asset"
done
if [ -d /var/www/html/wp-content/commerce-assets ]; then
  rmdir /var/www/html/wp-content/commerce-assets 2>/dev/null || true
fi
