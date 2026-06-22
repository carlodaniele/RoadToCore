<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Upload image from an asset reference.
 *
 * Supports:
 * - http/https URLs
 * - absolute local file paths
 *
 * @param string $asset_ref Image reference.
 * @param int    $post_id   Target post ID.
 * @param string $caption   Optional caption.
 * @param string $alt       Optional alt text.
 * @return int|\WP_Error Attachment ID on success.
 */
function roadtocore_upload_image_from_asset_ref( string $asset_ref, int $post_id, string $caption = '', string $alt = '' ) {
	$asset_ref = trim( $asset_ref );
	if ( '' === $asset_ref ) {
		return new \WP_Error( 'roadtocore_empty_asset_ref', 'Asset reference is empty.', array( 'status' => 400 ) );
	}

	$contents  = null;
	$file_name = null;
	$mime_type = 'image/jpeg';

	if ( preg_match( '#^https?://#i', $asset_ref ) ) {
		$response = wp_safe_remote_get(
			$asset_ref,
			array(
				'timeout' => 45,
			)
		);

		if ( is_wp_error( $response ) ) {
			return $response;
		}

		$code = wp_remote_retrieve_response_code( $response );
		if ( $code < 200 || $code >= 300 ) {
			return new \WP_Error( 'roadtocore_asset_download_failed', 'Could not download asset.', array( 'status' => 400 ) );
		}

		$contents  = wp_remote_retrieve_body( $response );
		$file_name = wp_basename( wp_parse_url( $asset_ref, PHP_URL_PATH ) ?: 'roadtocore-image.jpg' );
		$header_ct = (string) wp_remote_retrieve_header( $response, 'content-type' );
		if ( '' !== $header_ct ) {
			$mime_type = sanitize_mime_type( $header_ct );
		}
	} elseif ( str_starts_with( $asset_ref, '/' ) && file_exists( $asset_ref ) ) {
		$contents  = file_get_contents( $asset_ref );
		$file_name = wp_basename( $asset_ref );
		$checked   = wp_check_filetype( $file_name );
		if ( ! empty( $checked['type'] ) ) {
			$mime_type = sanitize_mime_type( (string) $checked['type'] );
		}
	} else {
		return new \WP_Error( 'roadtocore_unsupported_asset_ref', 'Unsupported asset_ref format.', array( 'status' => 400 ) );
	}

	if ( ! is_string( $contents ) || '' === $contents ) {
		return new \WP_Error( 'roadtocore_empty_asset_body', 'Asset body is empty.', array( 'status' => 400 ) );
	}

	$upload = wp_upload_bits( sanitize_file_name( $file_name ?: 'roadtocore-image.jpg' ), null, $contents );
	if ( ! empty( $upload['error'] ) ) {
		return new \WP_Error( 'roadtocore_upload_failed', (string) $upload['error'], array( 'status' => 500 ) );
	}

	$attachment_id = wp_insert_attachment(
		array(
			'post_mime_type' => $mime_type,
			'post_title'     => sanitize_text_field( pathinfo( $upload['file'], PATHINFO_FILENAME ) ),
			'post_excerpt'   => sanitize_text_field( $caption ),
			'post_status'    => 'inherit',
		),
		$upload['file'],
		$post_id
	);

	if ( is_wp_error( $attachment_id ) ) {
		return $attachment_id;
	}

	require_once ABSPATH . 'wp-admin/includes/image.php';
	$metadata = wp_generate_attachment_metadata( (int) $attachment_id, $upload['file'] );
	if ( is_array( $metadata ) ) {
		wp_update_attachment_metadata( (int) $attachment_id, $metadata );
	}

	if ( '' !== $alt ) {
		update_post_meta( (int) $attachment_id, '_wp_attachment_image_alt', sanitize_text_field( $alt ) );
	} elseif ( function_exists( 'wp_ai_client_prompt' ) ) {
		$alt_prompt = wp_ai_client_prompt( 'Generate concise alt text for this image in one sentence.' );
		if ( method_exists( $alt_prompt, 'is_supported_for_text_generation' ) && $alt_prompt->is_supported_for_text_generation() ) {
			$generated_alt = $alt_prompt->generate_text();
			if ( ! is_wp_error( $generated_alt ) ) {
				$generated_alt = sanitize_text_field( (string) $generated_alt );
				if ( '' !== $generated_alt ) {
					update_post_meta( (int) $attachment_id, '_wp_attachment_image_alt', $generated_alt );
				}
			}
		}
	}

	return (int) $attachment_id;
}

/**
 * Register REST routes.
 *
 * @return void
 */
function roadtocore_register_rest_routes(): void {
	register_rest_route(
		'roadtocore/v1',
		'/receive',
		array(
			'methods'             => \WP_REST_Server::CREATABLE,
			'callback'            => 'roadtocore_rest_receive',
			'permission_callback' => static function (): bool {
				return current_user_can( 'edit_posts' );
			},
		)
	);
}

/**
 * Process incoming RoadToCore payload.
 *
 * @param \WP_REST_Request $request Request.
 * @return \WP_REST_Response|\WP_Error
 */
function roadtocore_rest_receive( \WP_REST_Request $request ) {
	$payload = $request->get_json_params();

	if ( ! is_array( $payload ) ) {
		return new \WP_Error( 'roadtocore_invalid_payload', 'Payload must be a JSON object.', array( 'status' => 400 ) );
	}

	$schema_version  = isset( $payload['schema_version'] ) ? sanitize_text_field( (string) $payload['schema_version'] ) : '';
	$event_id        = isset( $payload['event_id'] ) ? sanitize_text_field( (string) $payload['event_id'] ) : '';
	$idempotency_key = isset( $payload['idempotency_key'] ) ? sanitize_text_field( (string) $payload['idempotency_key'] ) : '';
	$title           = isset( $payload['content']['title'] ) ? sanitize_text_field( (string) $payload['content']['title'] ) : '';
	$summary         = isset( $payload['content']['summary'] ) ? sanitize_textarea_field( (string) $payload['content']['summary'] ) : '';
	$transcript_full = isset( $payload['content']['transcript_full'] ) ? sanitize_textarea_field( (string) $payload['content']['transcript_full'] ) : '';
	$sections        = isset( $payload['content']['sections'] ) && is_array( $payload['content']['sections'] )
		? $payload['content']['sections']
		: array();
	$images          = isset( $payload['assets']['images'] ) && is_array( $payload['assets']['images'] )
		? $payload['assets']['images']
		: array();
	$post_status     = isset( $payload['targets']['wordpress']['post_status'] )
		? sanitize_key( (string) $payload['targets']['wordpress']['post_status'] )
		: 'draft';
	$allowed_post_statuses = array( 'draft', 'pending', 'publish' );
	if ( ! in_array( $post_status, $allowed_post_statuses, true ) ) {
		$post_status = 'draft';
	}

	if ( '' === $schema_version || ! str_starts_with( $schema_version, '1.1' ) ) {
		return new \WP_Error( 'roadtocore_invalid_schema_version', 'schema_version must start with 1.1.', array( 'status' => 400 ) );
	}

	if ( '' === $event_id ) {
		return new \WP_Error( 'roadtocore_missing_event_id', 'Missing event_id.', array( 'status' => 400 ) );
	}

	if ( '' === $idempotency_key ) {
		return new \WP_Error( 'roadtocore_missing_idempotency_key', 'Missing idempotency key.', array( 'status' => 400 ) );
	}

	if ( '' === $title ) {
		return new \WP_Error( 'roadtocore_missing_title', 'Missing content title.', array( 'status' => 400 ) );
	}

	$existing = get_posts(
		array(
			'post_type'      => 'post',
			'post_status'    => array( 'draft', 'pending', 'publish' ),
			'posts_per_page' => 1,
			'meta_key'       => '_roadtocore_idempotency_key',
			'meta_value'     => $idempotency_key,
		)
	);

	$post_content = roadtocore_build_post_content_from_sections( $sections );

	if ( ! empty( $existing ) ) {
		$post_id = (int) $existing[0]->ID;
		$update_result = wp_update_post(
			array(
				'ID'           => $post_id,
				'post_title'   => $title,
				'post_content' => $post_content,
				'post_excerpt' => $summary,
				'post_status'  => $post_status,
			),
			true
		);

		if ( is_wp_error( $update_result ) ) {
			return $update_result;
		}
	} else {
		$post_id = wp_insert_post(
			array(
				'post_type'    => 'post',
				'post_title'   => $title,
				'post_content' => $post_content,
				'post_excerpt' => $summary,
				'post_status'  => $post_status,
			),
			true
		);

		if ( is_wp_error( $post_id ) ) {
			return $post_id;
		}

		update_post_meta( (int) $post_id, '_roadtocore_idempotency_key', $idempotency_key );
	}

	update_post_meta( (int) $post_id, '_roadtocore_event_id', $event_id );
	if ( '' !== $transcript_full ) {
		update_post_meta( (int) $post_id, '_roadtocore_transcript_full', $transcript_full );
	}

	$attachment_ids = array();
	foreach ( $images as $image ) {
		if ( ! is_array( $image ) ) {
			continue;
		}

		$caption = isset( $image['caption'] ) ? (string) $image['caption'] : '';
		$alt     = isset( $image['alt'] ) ? (string) $image['alt'] : '';

		// Use wp_media_id if the image was already uploaded by the dispatcher
		if ( ! empty( $image['wp_media_id'] ) ) {
			$att_id = (int) $image['wp_media_id'];
			if ( '' !== $alt ) {
				update_post_meta( $att_id, '_wp_attachment_image_alt', sanitize_text_field( $alt ) );
			}
			$attachment_ids[] = $att_id;
			continue;
		}

		// Fallback: try to download from asset_ref (URL only, not local paths)
		$asset_ref = isset( $image['asset_ref'] ) ? (string) $image['asset_ref'] : '';
		if ( '' === $asset_ref || ! preg_match( '#^https?://#i', $asset_ref ) ) {
			continue;
		}

		$attachment_id = roadtocore_upload_image_from_asset_ref( $asset_ref, (int) $post_id, $caption, $alt );
		if ( is_wp_error( $attachment_id ) ) {
			continue;
		}

		$attachment_ids[] = (int) $attachment_id;
	}

	if ( ! empty( $attachment_ids ) ) {
		// Set featured image
		if ( ! has_post_thumbnail( (int) $post_id ) ) {
			set_post_thumbnail( (int) $post_id, $attachment_ids[0] );
		}

		// Append gallery blocks to post content
		$gallery_html = roadtocore_build_gallery_blocks( $attachment_ids, $images );
		if ( '' !== $gallery_html ) {
			$current_content = get_post_field( 'post_content', (int) $post_id );
			wp_update_post( array(
				'ID'           => (int) $post_id,
				'post_content' => $current_content . $gallery_html,
			) );
		}
	}

	return rest_ensure_response(
		array(
			'post_id'          => (int) $post_id,
			'idempotency_key'  => $idempotency_key,
			'attachments'      => $attachment_ids,
			'status'           => 'ok',
		)
	);
}
