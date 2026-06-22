"""EXIF metadata extraction from images.

Handles GPS coordinate extraction and image orientation correction.
Similar to nomad-pipeline's process_image_and_metadata logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ExifTags


@dataclass
class ImageMetadata:
    """Extracted image metadata."""
    latitude: float | None = None
    longitude: int | None = None
    orientation: int | None = None


def _get_decimal_from_dms(dms: tuple[Any, ...], ref: str) -> float:
    """Convert GPS DMS (degrees, minutes, seconds) to decimal coordinates."""
    try:
        degrees = float(dms[0])
        minutes = float(dms[1]) / 60.0
        seconds = float(dms[2]) / 3600.0
        decimal = degrees + minutes + seconds
        
        if ref in ['S', 'W']:
            return -decimal
        return decimal
    except (TypeError, IndexError, ZeroDivisionError):
        return 0.0


def extract_exif_metadata(image_bytes: bytes) -> ImageMetadata:
    """Extract GPS coordinates and orientation from EXIF data.
    
    Returns ImageMetadata with lat/lon (or None if not present).
    """
    metadata = ImageMetadata()
    
    try:
        img = Image.open(BytesIO(image_bytes))
        exif = img._getexif()  # type: ignore
        
        if not exif:
            return metadata
        
        # Create readable EXIF dict
        exif_dict = {ExifTags.TAGS.get(tag, tag): value for tag, value in exif.items()}
        
        # Extract orientation
        metadata.orientation = exif_dict.get('Orientation')
        
        # Extract GPS coordinates
        gps_info = exif_dict.get('GPSInfo')
        if gps_info:
            try:
                # GPSInfo structure: tag 1=N/S ref, 2=Lat, 3=E/W ref, 4=Lon
                lat = _get_decimal_from_dms(gps_info[2], gps_info[1])
                lon = _get_decimal_from_dms(gps_info[4], gps_info[3])
                metadata.latitude = lat
                metadata.longitude = lon
            except (KeyError, IndexError, TypeError) as exc:
                print(f"  WARNING: GPS coordinate extraction failed: {exc}")
    
    except Exception as exc:
        print(f"  WARNING: EXIF extraction failed: {exc}")
    
    return metadata


def rotate_image_by_exif(image_bytes: bytes) -> bytes:
    """Rotate image based on EXIF orientation tag.
    
    Returns the rotated image as bytes (JPEG, quality=85).
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        exif = img._getexif()  # type: ignore
        
        if exif:
            exif_dict = {ExifTags.TAGS.get(tag, tag): value for tag, value in exif.items()}
            orientation = exif_dict.get('Orientation')
            
            if orientation == 3:
                img = img.rotate(180, expand=True)
            elif orientation == 6:
                img = img.rotate(270, expand=True)
            elif orientation == 8:
                img = img.rotate(90, expand=True)
        
        # Compress and save
        output = BytesIO()
        img.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()
    
    except Exception as exc:
        print(f"  WARNING: Image rotation failed: {exc}")
        return image_bytes


def optimize_image(image_bytes: bytes, max_width: int = 1600) -> bytes:
    """Resize and optimize image for web.
    
    Reduces file size while maintaining quality. Similar to nomad-pipeline's resize_image.
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        
        if img.width > max_width:
            ratio = max_width / float(img.width)
            new_height = int(float(img.height) * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
        
        output = BytesIO()
        img.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue()
    
    except Exception as exc:
        print(f"  WARNING: Image optimization failed: {exc}")
        return image_bytes
