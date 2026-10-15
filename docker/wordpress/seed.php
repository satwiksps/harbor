<?php
// Repeatable deployment setup. Product edits and orders survive upgrades.
if (!class_exists('WooCommerce')) { throw new RuntimeException('WooCommerce is unavailable'); }
WC_Install::create_tables();
WC_Install::create_pages();
require_once '/opt/harbor/migrate-brand.php';
harbor_migrate_brand();
update_option('blogdescription', 'Useful things. Thoughtfully made.');
update_option('woocommerce_currency', 'INR');
update_option('woocommerce_default_country', 'IN:KA');
if (!get_option('woocommerce_store_address')) {
    update_option('woocommerce_store_address', 'Harbor Supply, Indiranagar');
}
update_option('woocommerce_store_city', 'Bengaluru');
update_option('woocommerce_store_postcode', '560038');
update_option('woocommerce_calc_taxes', 'no');
update_option('woocommerce_enable_guest_checkout', 'yes');
update_option('woocommerce_enable_signup_and_login_from_checkout', 'no');
update_option('woocommerce_allowed_countries', 'specific');
update_option('woocommerce_specific_allowed_countries', ['IN']);
update_option('woocommerce_ship_to_countries', '');
update_option('woocommerce_codemirror', 'no');
update_option('woocommerce_allow_tracking', 'no');
update_option('woocommerce_coming_soon', 'no');
update_option('woocommerce_store_pages_only', 'no');
update_option('woocommerce_task_list_hidden', 'yes');
update_option('woocommerce_onboarding_profile', ['completed' => true, 'skipped' => true]);
update_option('woocommerce_cod_settings', [
    'enabled' => 'yes', 'title' => 'Cash on delivery',
    'description' => 'Pay when your order arrives. This is a demonstration store; no payment is collected.',
    'instructions' => 'Demo order received. No payment or shipment will be processed.',
    'enable_for_methods' => [], 'enable_for_virtual' => 'yes',
]);
update_option('timezone_string', 'Asia/Kolkata');
update_option('show_on_front', 'page');
update_option('page_on_front', (int)get_option('woocommerce_shop_page_id'));
// Classic shortcodes keep the storefront flow reliable without a payment extension.
// Subsequent deployments must preserve pages edited by the store administrator.
if (!get_option('harbor_seed_complete')) {
    foreach (['cart' => '[woocommerce_cart]', 'checkout' => '[woocommerce_checkout]'] as $page => $content) {
        wp_update_post(['ID' => (int)get_option('woocommerce_' . $page . '_page_id'), 'post_content' => $content]);
    }
}
// Publish a working demo notice once. Future administrator edits remain intact.
if (!get_option('harbor_demo_privacy_page_id')) {
    $privacy = get_post((int)get_option('wp_page_for_privacy_policy'));
    if (!$privacy || $privacy->post_type !== 'page' || $privacy->post_status !== 'publish') {
        $privacy_id = wp_insert_post([
            'post_type' => 'page', 'post_status' => 'publish',
            'post_title' => 'Demo data use', 'post_name' => 'demo-data-use',
            'post_content' => '<h2>A local demonstration store</h2>'
                . '<p>Use invented names, addresses, and contact details when trying this store. Do not enter real personal or payment information.</p>'
                . '<p>Checkout details and test orders are saved in the store database. The store administrator can view them in WooCommerce, and authorized Harbor users can query order details through the connected tools.</p>'
                . '<p>Cash-on-delivery orders here are demonstrations. No payment is collected and no goods are delivered.</p>'
                . '<p>The person running this demonstration controls its stored records and local backups. Ask that person to remove any test records you no longer want retained.</p>',
        ], true);
        if (is_wp_error($privacy_id)) { throw new RuntimeException('Could not publish the demo data-use page'); }
        update_option('wp_page_for_privacy_policy', $privacy_id);
        update_option('woocommerce_checkout_privacy_policy_text', 'Use invented details for this demonstration. See our [privacy_policy] for how test records are stored.');
    } else {
        $privacy_id = $privacy->ID;
    }
    update_option('harbor_demo_privacy_page_id', $privacy_id);
}
$zone = new WC_Shipping_Zone(0);
$has_free = false;
$has_flat = false;
foreach ($zone->get_shipping_methods() as $method) {
    if ($method->id === 'free_shipping') {
        $has_free = true;
        update_option('woocommerce_free_shipping_' . $method->instance_id . '_settings', ['title' => 'Free delivery', 'requires' => 'min_amount', 'min_amount' => '1500', 'ignore_discounts' => 'no']);
    }
    if ($method->id === 'flat_rate') { $has_flat = true; }
}
if (!$has_free) {
    $instance = $zone->add_shipping_method('free_shipping');
    update_option('woocommerce_free_shipping_' . $instance . '_settings', ['title' => 'Free delivery', 'requires' => 'min_amount', 'min_amount' => '1500', 'ignore_discounts' => 'no']);
}
if (!$has_flat) {
    $instance = $zone->add_shipping_method('flat_rate');
    update_option('woocommerce_flat_rate_' . $instance . '_settings', ['title' => 'Standard delivery', 'tax_status' => 'none', 'cost' => '59']);
}
$user = get_user_by('login', getenv('WP_ADMIN_USER'));
if (!$user) { throw new RuntimeException('Store administrator does not exist'); }
$key = getenv('WOO_KEY');
$secret = getenv('WOO_SECRET');
if (!$key || !$secret) { throw new RuntimeException('WooCommerce credentials are missing'); }
global $wpdb;
$table = $wpdb->prefix . 'woocommerce_api_keys';
$hash = wc_api_hash($key);
$existing = $wpdb->get_var($wpdb->prepare("SELECT key_id FROM $table WHERE consumer_key = %s", $hash));
if (!$existing) {
    $wpdb->insert($table, [
        'user_id' => $user->ID, 'description' => 'Harbor gateway', 'permissions' => 'read_write',
        'consumer_key' => $hash, 'consumer_secret' => $secret, 'truncated_key' => substr($key, -7),
    ]);
    if ($wpdb->last_error) { throw new RuntimeException('Could not create the gateway REST credential'); }
}
$items = [
    ['HARBOR-TOTE-01', 'Everyday canvas tote', '799.00', 'An uncomplicated carryall for work, errands, and everything between. Natural canvas, roomy interior, reinforced handles.'],
    ['HARBOR-BOTTLE-01', 'Daily steel bottle', '1199.00', 'A durable companion for your desk and your next journey. Reusable stainless steel, 750 ml, easy-grip finish.'],
    ['HARBOR-NOTEBOOK-01', 'Notes for tomorrow', '349.00', 'A simple place for a new idea. A5 dotted pages, lay-flat binding, and a soft cover in warm stone.'],
];
foreach ($items as [$sku, $name, $price, $description]) {
    // A deleted starter item stays deleted after the initial seeding has completed.
    if (get_option('harbor_seed_complete') || wc_get_product_id_by_sku($sku)) { continue; }
    $product = new WC_Product_Simple();
    $product->set_name($name);
    $product->set_sku($sku);
    $product->set_regular_price($price);
    $product->set_description($description);
    $product->set_short_description($description);
    $product->set_status('publish');
    $product->set_catalog_visibility('visible');
    $product->set_manage_stock(true);
    $product->set_stock_quantity(100);
    $product->set_weight('0.5');
    $product->save();
}
// Replace default sample content only during initial seeding.
if (!get_option('harbor_seed_complete')) {
    foreach (['hello-world', 'sample-page'] as $slug) {
        $post = get_page_by_path($slug, OBJECT, ['post', 'page']);
        if ($post) { wp_trash_post($post->ID); }
    }
    update_option('sidebars_widgets', ['wp_inactive_widgets' => [], 'array_version' => 3]);
    update_option('harbor_seed_complete', '1');
}
flush_rewrite_rules(true);
echo "Store bootstrap complete; existing catalog and page edits were preserved.\n";
