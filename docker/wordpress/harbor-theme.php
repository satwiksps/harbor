<?php
/** Plugin Name: Harbor Supply storefront styling */
add_action('wp_enqueue_scripts', function () {
    $stylesheet = WP_CONTENT_DIR . '/harbor-assets/storefront.css';
    wp_enqueue_style(
        'harbor-storefront',
        content_url('/harbor-assets/storefront.css'),
        ['storefront-style', 'storefront-woocommerce-style'],
        is_file($stylesheet) ? (string)filemtime($stylesheet) : '1'
    );
    // The header count uses WooCommerce's native session and AJAX fragments.
    wp_enqueue_script('wc-cart-fragments');
}, 100);

add_action('after_setup_theme', function () {
    // Keep Storefront's outer container and both accessible skip links.
    remove_action('storefront_header', 'storefront_site_branding', 20);
    remove_action('storefront_header', 'storefront_secondary_navigation', 30);
    remove_action('storefront_header', 'storefront_product_search', 40);
    remove_action('storefront_header', 'storefront_primary_navigation_wrapper', 42);
    remove_action('storefront_header', 'storefront_primary_navigation', 50);
    remove_action('storefront_header', 'storefront_header_cart', 60);
    remove_action('storefront_header', 'storefront_primary_navigation_wrapper_close', 68);
    remove_action('storefront_footer', 'storefront_handheld_footer_bar', 999);
    remove_action('woocommerce_after_single_product_summary', 'storefront_single_product_pagination', 30);
    remove_filter('woocommerce_add_to_cart_fragments', 'storefront_cart_link_fragment');
}, 20);

function harbor_cart_link() {
    $count = WC()->cart ? (int)WC()->cart->get_cart_contents_count() : 0;
    $label = sprintf(_n('View cart, %d item', 'View cart, %d items', $count, 'harbor'), $count);
    return '<a class="harbor-cart-link" href="' . esc_url(wc_get_cart_url()) . '" aria-label="' . esc_attr($label) . '">'
        . '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true" focusable="false"><path d="M5 7h14l1 14H4L5 7Z"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/></svg>'
        . '<span>Cart</span><span class="harbor-cart-count" aria-hidden="true">' . esc_html($count) . '</span></a>';
}

add_action('storefront_header', function () {
    echo '<div class="harbor-header-inner">';
    echo '<a class="harbor-wordmark" href="' . esc_url(home_url('/')) . '">Harbor <span>Supply</span></a>';
    echo '<nav class="harbor-shop-nav" id="site-navigation" aria-label="Shop navigation" tabindex="-1">';
    echo '<a href="' . esc_url(wc_get_page_permalink('shop')) . '"' . (is_shop() ? ' aria-current="page"' : '') . '>Shop</a>';
    echo '<a href="' . esc_url(wc_get_page_permalink('myaccount')) . '"' . (is_account_page() ? ' aria-current="page"' : '') . '>My account</a>';
    echo '</nav><div class="harbor-header-search">';
    get_product_search_form();
    echo '</div>' . harbor_cart_link() . '</div>';
}, 20);

add_filter('woocommerce_add_to_cart_fragments', function ($fragments) {
    $fragments['a.harbor-cart-link'] = harbor_cart_link();
    return $fragments;
});

// Native AJAX inserts its View cart link beside the button inside this group.
add_action('woocommerce_after_shop_loop_item', function () {
    echo '<div class="harbor-product-actions">';
}, 9);
add_action('woocommerce_after_shop_loop_item', function () {
    echo '</div>';
}, 11);

add_action('woocommerce_before_main_content', function () {
    if (is_shop() && !is_search()) { echo '<p class="harbor-eyebrow">The everyday collection</p>'; }
}, 15);

add_action('storefront_before_header', function () {
    echo '<div class="harbor-demo">A demonstration store for Harbor. No real payments or deliveries.</div>';
});
add_action('woocommerce_archive_description', function () {
    if (is_shop() && !is_search()) { echo '<p class="harbor-intro">Totes, bottles and notebooks for everyday use. Browse the sample catalog or try a demo checkout.</p>'; }
}, 5);
add_filter('woocommerce_page_title', function ($title) { return is_shop() && !is_search() ? 'Made for your everyday.' : $title; });
add_filter('storefront_credit_link', '__return_false');
add_filter('pre_wp_mail', '__return_false'); // Demo setup has no mail provider.
function harbor_product_art($product) {
    $art = ['HARBOR-TOTE-01' => 'everyday-tote.svg', 'HARBOR-BOTTLE-01' => 'daily-bottle.svg', 'HARBOR-NOTEBOOK-01' => 'notes-notebook.svg'];
    return $product && isset($art[$product->get_sku()]) ? content_url('/harbor-assets/' . $art[$product->get_sku()]) : '';
}
function harbor_missing_product_image($product) {
    $name = trim(wp_strip_all_tags($product->get_name()));
    $words = preg_split('/\s+/u', $name, -1, PREG_SPLIT_NO_EMPTY);
    $initials = '';
    foreach (array_slice($words ?: [], 0, 2) as $word) {
        if (preg_match('/^./us', $word, $letter)) { $initials .= strtoupper($letter[0]); }
    }
    return '<span class="harbor-product-placeholder" role="img" aria-label="' . esc_attr('Image not supplied for ' . $name) . '"><span class="harbor-product-initials" aria-hidden="true">' . esc_html($initials ?: 'H') . '</span><span class="harbor-product-image-note" aria-hidden="true">Image not supplied</span></span>';
}
add_filter('woocommerce_product_get_image', function ($image, $product, $size, $attr, $placeholder, $original) {
    // Variations can inherit a real image or our fallback from their parent.
    if (!$product || $product->get_image_id() || $product->get_parent_id()) { return $image; }
    $url = harbor_product_art($product);
    if ($url && !$product->get_image_id()) {
        return '<img src="' . esc_url($url) . '" width="800" height="800" alt="' . esc_attr($product->get_name()) . '" loading="lazy" />';
    }
    if ($product && !$product->get_image_id() && $placeholder) {
        return harbor_missing_product_image($product);
    }
    return $image;
}, 10, 6);
add_filter('woocommerce_single_product_image_thumbnail_html', function ($html, $attachment_id) {
    global $product;
    $url = harbor_product_art($product);
    if ($url && !$attachment_id) {
        // Supply the same image metadata WooCommerce reads for native zoom and PhotoSwipe.
        return '<div class="woocommerce-product-gallery__image" data-thumb="' . esc_url($url) . '">'
            . '<a href="' . esc_url($url) . '"><img class="wp-post-image" src="' . esc_url($url) . '" width="800" height="800"'
            . ' data-large_image="' . esc_url($url) . '" data-large_image_width="800" data-large_image_height="800"'
            . ' alt="' . esc_attr($product->get_name()) . '" /></a></div>';
    }
    if ($product && !$attachment_id) {
        return '<div class="woocommerce-product-gallery__image--placeholder">' . harbor_missing_product_image($product) . '</div>';
    }
    return $html;
}, 10, 2);
// Keep checkout aligned with the documented sample shipping rate table.
add_filter('woocommerce_package_rates', function ($rates, $package) {
    foreach ($rates as $key => $rate) {
        if ($rate->method_id === 'free_shipping') { return [$key => $rate]; }
    }
    $weight = 0.0;
    foreach ($package['contents'] as $item) {
        $weight += ((float)wc_get_weight($item['data']->get_weight(), 'kg') ?: 0.5) * (int)$item['quantity'];
    }
    // Normalize computed floats before ceil: 0.4 + 0.8 + 0.3 must stay in the 1.5 kg band.
    $weight = (float)wc_format_decimal($weight);
    $remote = in_array($package['destination']['state'] ?? '', ['AN','AR','AS','LD','MN','ML','MZ','NL','SK','TR'], true);
    $cost = ($remote ? 99 : 59) + max(0, ceil(($weight - 0.5) / 0.5)) * 20;
    foreach ($rates as $rate) { if ($rate->method_id === 'flat_rate') { $rate->set_cost($cost); } }
    return $rates;
}, 100, 2);
