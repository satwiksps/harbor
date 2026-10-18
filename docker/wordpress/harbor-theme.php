<?php
/** Plugin Name: Harbor Supply storefront styling */
add_action('wp_head', function () {
    echo '<style>
    :root{--harbor-ink:#121517;--harbor-paper:#f3f2ed;--harbor-teal:#136b69;--harbor-line:#d6d3c8}
    body{background:var(--harbor-paper);color:var(--harbor-ink);font-family:Arial,Helvetica,sans-serif}
    .site-header{background:var(--harbor-paper);border-bottom:1px solid var(--harbor-line);padding-top:2rem}
    .site-branding .site-title a{color:var(--harbor-ink);font-size:1.8rem;letter-spacing:-.06em}
    .site-description{color:#686e68!important;font-size:.85rem}
    a{color:var(--harbor-teal)}
    .site-header-cart .cart-contents,.main-navigation ul li a{color:var(--harbor-ink)}
    button,.button,input[type=submit],a.button,.added_to_cart,button.alt,.button.alt,input[type=submit].alt,a.button.alt{background:var(--harbor-teal);color:white;border-radius:4px;box-shadow:none;font-weight:600}
    button:hover,.button:hover,a.button:hover,button.alt:hover,.button.alt:hover,input[type=submit].alt:hover,a.button.alt:hover{background:#0e5553;color:white}
    .woocommerce-breadcrumb{margin-bottom:1.5rem}.woocommerce-products-header{padding-bottom:2rem!important}
    h1,h2,h3{letter-spacing:-.045em;color:var(--harbor-ink)}h1{font-size:3.2rem;font-weight:500}
    ul.products li.product{background:#fff;border:1px solid var(--harbor-line);padding:1rem;border-radius:8px}
    ul.products li.product h2{font-size:1.25rem;letter-spacing:-.025em}
    ul.products li.product img{border-radius:4px;background:#eae9e3}
    .site-footer{background:#e8e7df;border-top:1px solid var(--harbor-line);color:#58605a;padding:2rem 0}
    .woocommerce-info,.woocommerce-message{background:var(--harbor-teal)}
    input[type=text],input[type=email],input[type=tel],input[type=password],textarea,select{background:#fff;border:1px solid var(--harbor-line);border-radius:4px;box-shadow:none}
    input[type=checkbox],input[type=radio]{accent-color:var(--harbor-teal)}
    a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--harbor-teal);outline-offset:2px}
    input:focus,textarea:focus{outline-color:var(--harbor-teal)}
    .widget-area{display:none}.content-area{width:100%!important;float:none!important;margin-right:0!important}
    .harbor-intro{color:#5a635b;max-width:36rem;margin-inline:auto;text-align:center;font-size:1.1rem}.harbor-demo{background:#e7eeeb;color:#1b5550;padding:.6rem;text-align:center;font-size:.8rem}
    .harbor-product-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1rem;aspect-ratio:1/1;width:100%;background:#eae9e3;border:1px solid var(--harbor-line);border-radius:4px;color:#5a635b;text-align:center}
    ul.products .harbor-product-placeholder{margin-bottom:1.618em}.harbor-product-initials{font-size:clamp(2.5rem,8vw,5rem);font-weight:500;letter-spacing:-.05em;color:var(--harbor-teal);line-height:1}.harbor-product-image-note{font-size:.8rem;letter-spacing:.025em}
    </style>';
});
add_action('storefront_before_header', function () {
    echo '<div class="harbor-demo">A demonstration store for Harbor. No real payments or deliveries.</div>';
});
add_action('woocommerce_archive_description', function () {
    if (is_shop()) { echo '<p class="harbor-intro">Totes, bottles and notebooks for everyday use. Browse the sample catalog or try a demo checkout.</p>'; }
}, 5);
add_filter('woocommerce_page_title', function ($title) { return is_shop() ? 'Made for your everyday.' : $title; });
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
        return '<div class="woocommerce-product-gallery__image"><img src="' . esc_url($url) . '" width="800" height="800" alt="' . esc_attr($product->get_name()) . '" /></div>';
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
