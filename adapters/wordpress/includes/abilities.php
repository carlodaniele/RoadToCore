<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Register RoadToCore abilities.
 *
 * @return void
 */
function roadtocore_register_abilities(): void {
	if ( ! function_exists( 'wp_register_ability' ) ) {
		return;
	}

	wp_register_ability(
		'roadtocore/publish-post',
		array(
			'label'       => __( 'Publish RoadToCore Payload', 'roadtocore' ),
			'description' => __( 'Creates or updates a post from a RoadToCore payload.', 'roadtocore' ),
			'category'    => 'site',
			'input_schema' => array(
				'type'       => 'object',
				'properties' => array(
					'schema_version'  => array( 'type' => 'string' ),
					'event_id'        => array( 'type' => 'string' ),
					'created_at'      => array( 'type' => 'string' ),
					'source'          => array( 'type' => 'object' ),
					'idempotency_key' => array( 'type' => 'string' ),
					'content'         => array( 'type' => 'object' ),
					'assets'          => array( 'type' => 'object' ),
					'meta'            => array( 'type' => 'object' ),
					'targets'         => array( 'type' => 'object' ),
					'ai_meta'         => array( 'type' => 'object' ),
				),
				'required'   => array( 'schema_version', 'event_id', 'idempotency_key', 'content', 'assets', 'meta', 'source' ),
			),
			'output_schema' => array(
				'type'       => 'object',
				'properties' => array(
					'post_id' => array( 'type' => 'integer' ),
					'status'  => array( 'type' => 'string' ),
				),
				'required'   => array( 'post_id', 'status' ),
			),
			'execute_callback' => 'roadtocore_execute_publish_ability',
			'permission_callback' => static function (): bool {
				return current_user_can( 'edit_posts' );
			},
			'meta' => array(
				'show_in_rest' => true,
			),
		)
	);
}

/**
 * Execute publish ability.
 *
 * @param array $input Ability input payload.
 * @return array|\WP_Error
 */
function roadtocore_execute_publish_ability( array $input ) {
	$request = new \WP_REST_Request( 'POST', '/roadtocore/v1/receive' );
	$request->set_body( wp_json_encode( $input ) );
	$request->set_header( 'content-type', 'application/json' );

	$result = roadtocore_rest_receive( $request );
	if ( is_wp_error( $result ) ) {
		return $result;
	}

	if ( $result instanceof \WP_REST_Response ) {
		return (array) $result->get_data();
	}

	return (array) $result;
}
