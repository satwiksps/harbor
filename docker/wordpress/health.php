<?php
header('Content-Type: application/json');
if (isset($_GET['ready'])) {
    mysqli_report(MYSQLI_REPORT_OFF);
    $parts = explode(':', getenv('WORDPRESS_DB_HOST'));
    $db = @new mysqli($parts[0], getenv('WORDPRESS_DB_USER'), getenv('WORDPRESS_DB_PASSWORD'), getenv('WORDPRESS_DB_NAME'), (int)($parts[1] ?? 3306));
    if ($db->connect_errno) {
        http_response_code(503);
        echo '{"status":"database_unavailable"}';
        exit;
    }
    $db->close();
}
echo '{"status":"ok"}';
