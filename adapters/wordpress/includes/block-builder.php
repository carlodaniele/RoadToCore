<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Build post_content from sections.
 *
 * @param array $sections Structured sections.
 * @return string
 */
function roadtocore_build_post_content_from_sections( array $sections ): string {
	$blocks = array();

	foreach ( $sections as $section ) {
		if ( ! is_array( $section ) ) {
			continue;
		}

		$heading = isset( $section['heading'] ) ? trim( (string) $section['heading'] ) : '';
		$level   = ( isset( $section['level'] ) && 3 === absint( $section['level'] ) ) ? 3 : 2;

		if ( '' !== $heading ) {
			$blocks[] = sprintf(
				'<!-- wp:heading {"level":%d} --><h%d>%s</h%d><!-- /wp:heading -->',
				$level,
				$level,
			esc_html( $heading ),
				$level
			);
		}

		if ( isset( $section['paragraphs'] ) && is_array( $section['paragraphs'] ) ) {
			foreach ( $section['paragraphs'] as $paragraph ) {
				$text = trim( (string) $paragraph );
				if ( '' === $text ) {
					continue;
				}
				$blocks[] = '<!-- wp:paragraph --><p>' . esc_html( $text ) . '</p><!-- /wp:paragraph -->';
			}
		}

		if ( isset( $section['bullet_points'] ) && is_array( $section['bullet_points'] ) ) {
			$items = array();
			foreach ( $section['bullet_points'] as $bullet_point ) {
				$text = trim( (string) $bullet_point );
				if ( '' === $text ) {
					continue;
				}
				$items[] = '<li>' . esc_html( $text ) . '</li>';
			}

			if ( ! empty( $items ) ) {
				$blocks[] = '<!-- wp:list --><ul>' . implode( '', $items ) . '</ul><!-- /wp:list -->';
			}
		}
	}

	return implode( "\n\n", $blocks );
}

/**
 * Build Gutenberg gallery blocks from attachment IDs and URLs.
 *
 * @param array $attachment_ids List of WP attachment IDs.
 * @param array $images         Original image asset data (for alt/caption/gps).
 * @return string
 */
function roadtocore_build_gallery_blocks( array $attachment_ids, array $images = array() ): string {
	if ( empty( $attachment_ids ) ) {
		return '';
	}

	$image_blocks = array();
	foreach ( $attachment_ids as $i => $att_id ) {
		$url     = wp_get_attachment_url( $att_id );
		$alt     = isset( $images[ $i ]['alt'] ) ? esc_attr( (string) $images[ $i ]['alt'] ) : '';
		$caption = isset( $images[ $i ]['caption'] ) ? esc_html( (string) $images[ $i ]['caption'] ) : '';

		// Append GPS coordinates to caption if available
		if ( isset( $images[ $i ]['gps']['latitude'], $images[ $i ]['gps']['longitude'] ) ) {
			$lat      = round( (float) $images[ $i ]['gps']['latitude'], 4 );
			$lon      = round( (float) $images[ $i ]['gps']['longitude'], 4 );
			$gps_text = "({$lat}, {$lon})";
			$caption  = $caption ? "{$caption} {$gps_text}" : $gps_text;
		}

		$figcaption = $caption ? "<figcaption class=\"wp-element-caption\">{$caption}</figcaption>" : '';

		$image_blocks[] = sprintf(
			'<!-- wp:image {"id":%d,"lightbox":{"enabled":true},"sizeSlug":"large","linkDestination":"none"} -->' .
			'<figure class="wp-block-image size-large"><img src="%s" alt="%s" class="wp-image-%d"/>%s</figure>' .
			'<!-- /wp:image -->',
			$att_id,
			esc_url( $url ),
			$alt,
			$att_id,
			$figcaption
		);
	}

	if ( count( $image_blocks ) === 1 ) {
		return "\n\n" . $image_blocks[0];
	}

	$inner = implode( "\n", $image_blocks );
	return "\n\n<!-- wp:gallery {\"linkTo\":\"none\"} -->\n" .
		"<figure class=\"wp-block-gallery has-nested-images columns-default is-cropped\">\n" .
		$inner . "\n" .
		"</figure>\n" .
		"<!-- /wp:gallery -->";
}
