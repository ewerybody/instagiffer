"""
Configuration schema for Instagiffer.

Uses Pydantic for validation and type safety.
"""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PathsConfig(BaseModel):
    """File path configuration."""

    working_dir: Path | None = Field(
        description='Custom working directory for temporary files'
    )
    gif_output_path: Path = Field(description='Default output path for generated GIFs')
    fail_safe_dir: Path | None = Field(
        description='Fallback directory for non-latin locales (platform-specific if not set)'
    )

    @field_validator('working_dir', 'gif_output_path', 'fail_safe_dir', mode='before')
    @classmethod
    def expand_path(cls, v: str | Path | None) -> Path | None:
        """Expand environment variables and user paths."""
        if v is None:
            return None
        path = Path(v).expanduser()
        # Expand environment variables
        return Path(str(path).format(**dict(os.environ)))


class VideoConfig(BaseModel):
    """Video processing configuration."""

    fps: float = Field(ge=1.0, le=60.0, description='Target frames per second')
    quality: int = Field(ge=1, le=100, description='Video quality (1-100)')
    max_width: int = Field(ge=100, le=4000, description='Maximum output width')
    max_height: int = Field(ge=100, le=4000, description='Maximum output height')
    maintain_aspect_ratio: bool = Field(
        description='Maintain aspect ratio when resizing'
    )


class GifConfig(BaseModel):
    """GIF generation configuration."""

    max_file_size_mb: float = Field(ge=0.1, le=50.0, description='Target max file size')
    loop_count: int = Field(ge=0, le=65535, description='Loop count (0 = infinite)')
    optimize: bool = Field(description='Optimize GIF file size')
    dither: Literal['none', 'floyd_steinberg', 'bayer'] = Field(
        description='Dithering algorithm'
    )
    colors: int = Field(ge=2, le=256, description='Number of colors in palette')


class EffectsConfig(BaseModel):
    """Visual effects configuration."""

    blur_radius: float = Field(ge=0.0, le=10.0, description='Blur effect radius')
    brightness: float = Field(ge=0.0, le=2.0, description='Brightness multiplier')
    contrast: float = Field(ge=0.0, le=2.0, description='Contrast multiplier')
    saturation: float = Field(ge=0.0, le=2.0, description='Saturation multiplier')
    grayscale: bool = Field(description='Convert to grayscale')
    sepia: bool = Field(description='Apply sepia effect')


class TextOverlayConfig(BaseModel):
    """Text overlay configuration."""

    enabled: bool = Field(description='Enable text overlay')
    text: str = Field(description='Text to overlay')
    font_family: str = Field(description='Font family name')
    font_size: int = Field(ge=8, le=200, description='Font size in pixels')
    color: str = Field(description='Text color (hex)')
    position: Literal['top', 'center', 'bottom'] = Field(description='Text position')
    background_alpha: float = Field(ge=0.0, le=1.0, description='Background opacity')


class SettingsConfig(BaseModel):
    """General application settings."""

    overwrite_gif: bool = Field(
        description='Overwrite existing GIF or create new numbered file'
    )
    auto_play_preview: bool = Field(description='Auto-play preview after generation')
    show_advanced_options: bool = Field(description='Show advanced options in UI')
    theme: Literal['light', 'dark', 'system'] = Field(description='UI theme')
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR'] = Field(
        description='Logging level'
    )


class InstaConfig(BaseModel):
    """Main Instagiffer configuration."""

    paths: PathsConfig
    video: VideoConfig
    gif: GifConfig
    effects: EffectsConfig
    text: TextOverlayConfig
    settings: SettingsConfig

    model_config = {
        'json_schema_extra': {
            'example': {
                'video': {'fps': 15, 'quality': 85},
                'gif': {'max_file_size_mb': 1.0, 'optimize': True},
            }
        }
    }
