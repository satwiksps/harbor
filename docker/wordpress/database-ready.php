<?php
mysqli_report(MYSQLI_REPORT_OFF);
$parts = explode(':', getenv('WORDPRESS_DB_HOST'));
$db = @new mysqli($parts[0], getenv('WORDPRESS_DB_USER'), getenv('WORDPRESS_DB_PASSWORD'), getenv('WORDPRESS_DB_NAME'), (int)($parts[1] ?? 3306));
exit($db->connect_errno ? 1 : 0);
