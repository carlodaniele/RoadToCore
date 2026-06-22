<?php
/**
 * Plugin Name: RoadToCore Adapter
 * Description: Receives RoadToCore payloads and publishes WordPress drafts.
 * Version: 0.2.0
 * Requires at least: 7.0
 * Requires PHP: 8.0
 * Author: RoadToCore
 * License: GPL-2.0-or-later
 * Text Domain: roadtocore
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

require_once __DIR__ . '/includes/block-builder.php';
require_once __DIR__ . '/includes/rest-api.php';
require_once __DIR__ . '/includes/abilities.php';

add_action( 'rest_api_init', 'roadtocore_register_rest_routes' );
add_action( 'wp_abilities_api_init', 'roadtocore_register_abilities' );
