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
