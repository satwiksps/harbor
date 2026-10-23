<?php
// Isolated state doubles. This never loads WordPress or connects to a database.
require __DIR__ . '/../docker/wordpress/migrate-brand.php';

function check($condition, $message) { if (!$condition) { throw new RuntimeException($message); } }
function get_option($key, $default = false) { global $options; return $options[$key] ?? $default; }
function update_option($key, $value) { global $options; $options[$key] = $value; }
function delete_option($key) { global $options; unset($options[$key]); }
function get_post($id) { global $posts; return isset($posts[$id]) ? (object)$posts[$id] : null; }
function wp_update_post($values, $error = false) { global $posts; $posts[$values['ID']] = array_merge($posts[$values['ID']], $values); return $values['ID']; }
function is_wp_error($value) { return false; }
function wc_get_product_id_by_sku($sku) { global $products; foreach ($products as $id => $product) { if ($product['sku'] === $sku) { return $id; } } return 0; }
function wc_get_product($id) { return new ProductDouble($id); }
class ProductDouble {
    public function __construct(private int $id) {}
    public function set_sku($sku) { global $products; $products[$this->id]['sku'] = $sku; }
    public function save() { return $this->id; }
}
class DatabaseDouble {
    public string $prefix = 'wp_';
    public string $last_error = '';
    public function prepare($query, ...$arguments) { return $query; }
    public function get_var($query) { return 1; }
    public function update($table, $set, $where) {
        global $api_keys;
        foreach ($api_keys as &$key) {
            if ($key['description'] === $where['description']) { $key['description'] = $set['description']; }
        }
    }
}
$wpdb = new DatabaseDouble();
$options = [
    'commerce_seed_complete' => '1', 'commerce_demo_privacy_page_id' => 11,
    'blogname' => 'Urumi Supply', 'woocommerce_store_address' => 'Urumi Supply, Indiranagar',
];
$products = [
    21 => ['sku' => 'URUMI-TOTE-01', 'name' => 'Edited tote', 'price' => '987.00', 'stock' => 19],
    22 => ['sku' => 'CUSTOM-01', 'name' => 'Custom item', 'price' => '50.00', 'stock' => 5],
];
$posts = [11 => ['ID' => 11, 'post_content' => 'Before: authorized Commerce console users. User-added paragraph.']];
$api_keys = [['description' => 'Commerce gateway', 'consumer_key' => 'unchanged-hash', 'consumer_secret' => 'unchanged-secret']];
harbor_migrate_brand();
check(array_keys($products) === [21, 22], 'Product IDs and deleted starter items must stay unchanged');
check($products[21] === ['sku' => 'HARBOR-TOTE-01', 'name' => 'Edited tote', 'price' => '987.00', 'stock' => 19], 'SKU migration must preserve product edits');
check($products[22]['sku'] === 'CUSTOM-01', 'Custom SKUs must stay unchanged');
check(get_option('harbor_seed_complete') === '1' && get_option('commerce_seed_complete') === '1', 'Seed state must migrate');
check(get_option('harbor_demo_privacy_page_id') === 11, 'Privacy page identity must stay unchanged');
check(get_option('blogname') === 'Harbor Supply', 'Default title must migrate');
check($posts[11]['post_content'] === 'Before: authorized Harbor users. User-added paragraph.', 'Privacy edits must survive');
check($api_keys[0] === ['description' => 'Harbor gateway', 'consumer_key' => 'unchanged-hash', 'consumer_secret' => 'unchanged-secret'], 'Credential bytes must stay unchanged');
$snapshot = serialize([$options, $products, $posts, $api_keys]);
harbor_migrate_brand();
check($snapshot === serialize([$options, $products, $posts, $api_keys]), 'A repeated upgrade must be idempotent');
echo "PASS: existing catalog, seed state, privacy edits and credentials survive the rename\n";

$options = ['blogname' => 'Custom shop', 'woocommerce_store_address' => 'Custom address'];
$products = $posts = $api_keys = [];
harbor_migrate_brand();
check($options === ['blogname' => 'Custom shop', 'woocommerce_store_address' => 'Custom address'], 'Custom store identity must stay unchanged');
echo "PASS: fresh installs and custom store identities remain untouched by migration\n";

$products = [1 => ['sku' => 'URUMI-TOTE-01'], 2 => ['sku' => 'HARBOR-TOTE-01']];
$failed = false;
try { harbor_migrate_brand(); } catch (RuntimeException $error) { $failed = str_contains($error->getMessage(), 'SKU conflict'); }
check($failed && count($products) === 2 && $products[1]['sku'] === 'URUMI-TOTE-01', 'Conflicting SKUs must fail without merging products');
echo "PASS: conflicting starter SKUs stop the migration without merging records\n";

$actions = $filters = [];
function add_action($name, $callback, $priority = 10) { global $actions; $actions[$name] = $callback; }
function add_filter($name, $callback, $priority = 10, $arguments = 1) { global $filters; $filters[$name] = $callback; }
function content_url($path) { return 'https://store.example.test/wp-content' . $path; }
require __DIR__ . '/../docker/wordpress/harbor-theme.php';
check(isset($actions['woocommerce_archive_description']), 'Storefront intro must use the WooCommerce hook');
foreach (['woocommerce_page_title', 'woocommerce_product_get_image', 'woocommerce_single_product_image_thumbnail_html', 'woocommerce_package_rates'] as $hook) {
    check(isset($filters[$hook]), 'Missing upstream integration hook: ' . $hook);
}
$starter = new class { public function get_sku() { return 'HARBOR-TOTE-01'; } };
check(harbor_product_art($starter) === 'https://store.example.test/wp-content/harbor-assets/everyday-tote.svg', 'Starter art must resolve the new SKU and packaged asset path');
echo "PASS: WooCommerce integration hooks and Harbor product art remain connected\n";

// Execute the actual bootstrap against isolated store state, including WooCommerce's
// default pounds setting. A repeated bootstrap must not reinterpret existing weights.
class WooCommerce {}
class WC_Install {
    public static function create_tables() {}
    public static function create_pages() {}
}
class WC_Shipping_Zone {
    public function __construct($id) {}
    public function get_shipping_methods() {
        return [(object)['id' => 'free_shipping', 'instance_id' => 1], (object)['id' => 'flat_rate', 'instance_id' => 2]];
    }
}
class WC_Product_Simple {
    private array $values = [];
    public function __call($method, $arguments) {
        check(str_starts_with($method, 'set_'), 'Unexpected product operation');
        $this->values[substr($method, 4)] = $arguments[0];
    }
    public function save() { global $products; $products[] = $this->values; }
}
function get_user_by($field, $value) { return (object)['ID' => 1]; }
function wc_api_hash($value) { return hash('sha256', $value); }
function get_page_by_path($slug, $output, $types) { return null; }
function flush_rewrite_rules($hard) {}
define('OBJECT', 'OBJECT');
putenv('WP_ADMIN_USER=test-admin');
putenv('WOO_KEY=test-key');
putenv('WOO_SECRET=test-secret');
$options = [
    'woocommerce_weight_unit' => 'lbs', 'woocommerce_cart_page_id' => 1,
    'woocommerce_checkout_page_id' => 2, 'harbor_demo_privacy_page_id' => 3,
];
$posts = [1 => ['ID' => 1], 2 => ['ID' => 2]];
$products = $api_keys = [];
ob_start();
require __DIR__ . '/../docker/wordpress/seed.php';
ob_end_clean();
check(get_option('woocommerce_weight_unit') === 'kg', 'A new demo store must interpret starter weights as kilograms');
check(count($products) === 3 && array_column($products, 'weight') === ['0.5', '0.5', '0.5'], 'Each starter product must weigh 0.5 kg');
$catalog = serialize($products);
update_option('woocommerce_weight_unit', 'lbs');
ob_start();
require __DIR__ . '/../docker/wordpress/seed.php';
ob_end_clean();
check(get_option('woocommerce_weight_unit') === 'lbs', 'Repeated bootstrap must preserve administrator-selected units');
check(serialize($products) === $catalog, 'Repeated bootstrap must preserve the existing catalog');
echo "PASS: fresh bootstrap sets kilograms; repeat bootstrap preserves configured units and products\n";

// Exercise the registered checkout filter with WooCommerce's unit conversion and float precision.
function wc_get_weight($weight, $unit) {
    check($unit === 'kg', 'Rates must use kilograms');
    return (float)$weight * (get_option('woocommerce_weight_unit') === 'lbs' ? 0.45359237 : 1);
}
function wc_format_decimal($value) { return number_format((float)$value, 6, '.', ''); }
class ShippingRateDouble {
    public function __construct(public string $method_id, public float $cost) {}
    public function set_cost($cost) { $this->cost = (float)$cost; }
}
$shipping_filter = $filters['woocommerce_package_rates'];
update_option('woocommerce_weight_unit', 'kg');
foreach ([
    ['standard first band', [['0.5', 1]], 'KA', 59.0],
    ['remote first band', [['0.5', 1]], 'AS', 99.0],
    ['started second band', [['0.51', 1]], 'KA', 79.0],
    ['quantity weight', [['0.5', 3]], 'KA', 99.0],
    ['two starter products', [['0.5', 2]], 'KA', 79.0],
    ['default missing weight', [['', 1]], 'KA', 59.0],
    ['decimal boundary', [['0.4', 1], ['0.8', 1], ['0.3', 1]], 'KA', 99.0],
    ['remote decimal boundary', [['0.4', 1], ['0.8', 1], ['0.3', 1]], 'AS', 139.0],
    ['just above boundary', [['1.500001', 1]], 'KA', 119.0],
] as [$case, $items, $state, $expected]) {
    $contents = [];
    foreach ($items as [$weight, $quantity]) {
        $contents[] = [
            'data' => new class($weight) {
                public function __construct(private string $weight) {}
                public function get_weight() { return $this->weight; }
            },
            'quantity' => $quantity,
        ];
    }
    $rates = $shipping_filter(
        ['flat' => new ShippingRateDouble('flat_rate', 0), 'pickup' => new ShippingRateDouble('local_pickup', 0)],
        ['contents' => $contents, 'destination' => ['state' => $state]],
    );
    check($rates['flat']->cost === $expected, 'Incorrect checkout price: ' . $case);
    check($rates['pickup']->cost === 0.0, 'Only the sample flat rate may change');
}
$free = new ShippingRateDouble('free_shipping', 0);
$rates = $shipping_filter(
    ['flat' => new ShippingRateDouble('flat_rate', 59), 'free' => $free],
    ['contents' => [], 'destination' => ['state' => 'KA']],
);
check($rates === ['free' => $free], 'An available free-delivery rate must replace paid rates');
echo "PASS: checkout rates preserve decimal boundaries, quantities, remote regions and free delivery\n";
