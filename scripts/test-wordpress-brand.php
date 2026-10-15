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
