<?php
// Upgrade only known demonstration branding. Keep IDs, orders, edits and seed state.
function harbor_migrate_brand() {
    foreach (['seed_complete', 'demo_privacy_page_id'] as $suffix) {
        $old = get_option('commerce_' . $suffix, false);
        if ($old !== false && get_option('harbor_' . $suffix, false) === false) {
            update_option('harbor_' . $suffix, $old);
        }
        // Keep the legacy seed marker so older code cannot recreate deleted products.
    }
    foreach (['TOTE', 'BOTTLE', 'NOTEBOOK'] as $item) {
        $old_id = wc_get_product_id_by_sku('URUMI-' . $item . '-01');
        if (!$old_id) { continue; }
        $sku = 'HARBOR-' . $item . '-01';
        $new_id = wc_get_product_id_by_sku($sku);
        if ($new_id && $new_id !== $old_id) {
            throw new RuntimeException('Starter product SKU conflict; review the catalog before retrying.');
        }
        $product = wc_get_product($old_id);
        if (!$product) { throw new RuntimeException('Could not load starter product for branding upgrade.'); }
        $product->set_sku($sku);
        $product->save();
    }
    foreach ([
        'blogname' => ['Urumi Supply', 'Harbor Supply'],
        'woocommerce_store_address' => ['Urumi Supply, Indiranagar', 'Harbor Supply, Indiranagar'],
    ] as $option => [$old, $new]) {
        if (get_option($option) === $old) { update_option($option, $new); }
    }
    $privacy_id = (int)get_option('harbor_demo_privacy_page_id');
    $privacy = $privacy_id ? get_post($privacy_id) : null;
    if ($privacy) {
        $content = str_replace('authorized Commerce console users', 'authorized Harbor users', $privacy->post_content);
        if ($content !== $privacy->post_content) {
            $updated = wp_update_post(['ID' => $privacy_id, 'post_content' => $content], true);
            if (is_wp_error($updated)) { throw new RuntimeException('Could not update the demonstration data-use page.'); }
        }
    }
    global $wpdb;
    $wpdb->update($wpdb->prefix . 'woocommerce_api_keys', ['description' => 'Harbor gateway'], ['description' => 'Commerce gateway']);
    if ($wpdb->last_error) { throw new RuntimeException('Could not update the gateway credential description.'); }
}
