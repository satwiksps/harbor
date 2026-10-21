FROM wordpress:cli-2.12.0-php8.3 AS cli
FROM wordpress:7.1.3-php8.3-apache

ARG WOOCOMMERCE_VERSION=11.2.1
ARG WOOCOMMERCE_SHA256=5317434cb381b5fdc7c1dd6ee258324f08c390976aaf8d1199f2a0e9a3ab8586
ARG STOREFRONT_VERSION=4.6.2
ARG STOREFRONT_SHA256=163b5bf217dad76a823432d8c01598ab3dc3f15cbdd69067b98f5a6ddf05c1c4
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --show-error --location "https://downloads.wordpress.org/plugin/woocommerce.${WOOCOMMERCE_VERSION}.zip" -o /tmp/woocommerce.zip \
    && echo "${WOOCOMMERCE_SHA256}  /tmp/woocommerce.zip" | sha256sum -c - \
    && unzip -q /tmp/woocommerce.zip -d /usr/src/wordpress/wp-content/plugins \
    && curl --fail --show-error --location "https://downloads.wordpress.org/theme/storefront.${STOREFRONT_VERSION}.zip" -o /tmp/storefront.zip \
    && echo "${STOREFRONT_SHA256}  /tmp/storefront.zip" | sha256sum -c - \
    && unzip -q /tmp/storefront.zip -d /usr/src/wordpress/wp-content/themes \
    && rm /tmp/woocommerce.zip /tmp/storefront.zip \
    && sed -i 's/Listen 80/Listen 8080/' /etc/apache2/ports.conf \
    && sed -i 's/:80>/:8080>/' /etc/apache2/sites-available/000-default.conf \
    && printf '\nServerName localhost\n' >> /etc/apache2/apache2.conf \
    && mkdir -p /usr/src/wordpress/wp-content/mu-plugins /var/run/apache2 /var/lock/apache2 /opt/harbor \
    && chown -R www-data:www-data /var/run/apache2 /var/lock/apache2 /usr/src/wordpress
COPY --from=cli /usr/local/bin/wp /usr/local/bin/wp
COPY --chmod=644 installer-deny.conf /etc/apache2/conf-available/harbor-installer-deny.conf
COPY --chown=33:33 bootstrap.sh seed.php migrate-brand.php sync-packaged.sh database-ready.php /opt/harbor/
COPY --chown=33:33 health.php /usr/src/wordpress/health.php
COPY --chown=33:33 harbor-theme.php /usr/src/wordpress/wp-content/mu-plugins/harbor-theme.php
COPY --chown=33:33 assets/ /usr/src/wordpress/wp-content/harbor-assets/
RUN a2enconf harbor-installer-deny \
    && chmod 755 /opt/harbor/bootstrap.sh \
    && php -l /opt/harbor/seed.php \
    && php -l /opt/harbor/migrate-brand.php \
    && php -l /opt/harbor/database-ready.php \
    && php -l /usr/src/wordpress/health.php \
    && php -l /usr/src/wordpress/wp-content/mu-plugins/harbor-theme.php
USER 33:33
EXPOSE 8080
