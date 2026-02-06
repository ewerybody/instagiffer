"""
Configuration manager for Instagiffer.

Implements layered configuration:
1. Default settings (shipped with app - read-only)
2. User settings (overrides defaults)
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schema import InstaConfig

logger = logging.getLogger(__name__)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two dictionaries, with override taking precedence.

    Args:
        base: Base dictionary
        override: Override dictionary (takes precedence)

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


class ConfigManager:
    """Manages application configuration with layered overrides."""

    DEFAULT_CONFIG_NAME = 'instagiffer_config.json'

    def __init__(self, user_config_path: Path | str | None = None):
        """
        Initialize config manager.

        Args:
            user_config_path: Path to user config file. If None, uses default location.
        """
        self.defaults_path = self._get_defaults_path()
        self.user_config_path = self._resolve_user_config_path(user_config_path)

        # Load configuration layers
        self.defaults = self._load_defaults()
        self.user_overrides = self._load_user_config()

        # Merge and create final config
        self.config: InstaConfig = self._create_merged_config()

    @staticmethod
    def _get_defaults_path() -> Path:
        """Get path to shipped defaults.json."""
        # defaults.json is in the same directory as this file
        return Path(__file__).parent / 'defaults.json'

    @staticmethod
    def _resolve_user_config_path(config_path: Path | str | None) -> Path:
        """Resolve user config file path."""
        if config_path is None:
            # Use platform-appropriate config directory
            config_dir = Path.home() / '.config' / 'instagiffer'
            config_dir.mkdir(parents=True, exist_ok=True)
            return config_dir / ConfigManager.DEFAULT_CONFIG_NAME

        return Path(config_path)

    def _load_defaults(self) -> dict[str, Any]:
        """Load default configuration from shipped defaults.json."""
        if not self.defaults_path.exists():
            raise RuntimeError(
                f'defaults.json not found at {self.defaults_path}. '
                'This file should be shipped with the application.'
            )

        logger.info(f'Loading defaults from {self.defaults_path}')

        with open(self.defaults_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _load_user_config(self) -> dict[str, Any]:
        """Load user configuration overrides."""
        if not self.user_config_path.exists():
            logger.info(f'No user config found at {self.user_config_path}')
            return {}

        try:
            logger.info(f'Loading user config from {self.user_config_path}')
            with open(self.user_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f'Failed to load user config: {e}. Using defaults.')
            return {}

    def _create_merged_config(self) -> InstaConfig:
        """Create final config by merging defaults and user overrides."""
        # Deep merge user overrides into defaults
        merged = deep_merge(self.defaults, self.user_overrides)

        # Validate and create config
        try:
            return InstaConfig.model_validate(merged)
        except ValidationError as e:
            logger.error(f'Invalid configuration: {e}')
            logger.warning('Falling back to defaults only')
            return InstaConfig.model_validate(self.defaults)

    def save_user_config(self, full_config: bool = False) -> None:
        """
        Save user configuration.

        Args:
            full_config: If True, saves complete config. If False, only saves overrides.
        """
        # Ensure directory exists
        self.user_config_path.parent.mkdir(parents=True, exist_ok=True)

        if full_config:
            # Save complete configuration (for debugging/export)
            data = self.config.model_dump(mode='json')
            logger.info(f'Saving full config to {self.user_config_path}')
        else:
            # Save only user's overrides (VS Code style)
            data = self._compute_overrides()

            # Auto-cleanup: if no overrides, delete the user config file
            if not data:
                logger.info('No user overrides - removing user config file')
                if self.user_config_path.exists():
                    self.user_config_path.unlink()
                return

            logger.info(f'Saving user config to {self.user_config_path}')

        with open(self.user_config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info('User config saved successfully')

    def _compute_overrides(self) -> dict[str, Any]:
        """
        Compute which settings differ from defaults (user overrides only).

        This keeps the user config file minimal - only changed settings.
        Handles path expansion properly so expanded paths don't look like overrides.
        """
        current = self.config.model_dump(mode='json')
        overrides: dict[str, Any] = {}

        for section, values in current.items():
            if section not in self.defaults:
                # New section not in defaults - include it
                overrides[section] = values
                continue

            section_overrides = {}

            if isinstance(values, dict):
                for key, value in values.items():
                    default_value = self.defaults[section].get(key)

                    # Special handling for paths - compare normalized versions
                    if isinstance(value, str) and (
                        isinstance(default_value, str) or default_value is None
                    ):
                        # Check if this looks like a path
                        if default_value and (
                            '/' in default_value
                            or '\\' in default_value
                            or '~' in default_value
                        ):
                            # Normalize both for comparison
                            try:
                                from pathlib import Path

                                normalized_current = str(
                                    Path(value).expanduser().resolve()
                                )
                                normalized_default = str(
                                    Path(default_value).expanduser().resolve()
                                )

                                if normalized_current == normalized_default:
                                    continue  # Same path, don't include in overrides
                            except Exception:
                                # If normalization fails, fall through to regular comparison
                                pass

                    # Regular comparison for non-paths
                    if value != default_value:
                        section_overrides[key] = value
            else:
                # Non-dict value
                if values != self.defaults.get(section):
                    overrides[section] = values

            if section_overrides:
                overrides[section] = section_overrides

        return overrides

    def update(self, **kwargs: Any) -> None:
        """
        Update specific config values and save.

        Example:
            config_manager.update(video__fps=20, gif__optimize=False)
        """
        # Convert flat kwargs to nested dict
        nested_updates: dict[str, Any] = {}

        for key, value in kwargs.items():
            if '__' in key:
                section, field = key.split('__', 1)
                if section not in nested_updates:
                    nested_updates[section] = {}
                nested_updates[section][field] = value
            else:
                nested_updates[key] = value

        # Merge updates into user overrides
        self.user_overrides = deep_merge(self.user_overrides, nested_updates)

        # Recreate merged config
        self.config = self._create_merged_config()

        # Save user config (only overrides)
        self.save_user_config(full_config=False)

    def reset_to_defaults(self) -> None:
        """Reset all settings to defaults (deletes user config)."""
        logger.info('Resetting configuration to defaults')

        # Clear user overrides
        self.user_overrides = {}

        # Recreate config from defaults only
        self.config = InstaConfig.model_validate(self.defaults)

        # Delete user config file
        if self.user_config_path.exists():
            self.user_config_path.unlink()
            logger.info(f'Deleted user config: {self.user_config_path}')

    def reload(self) -> None:
        """Reload configuration from files."""
        self.user_overrides = self._load_user_config()
        self.config = self._create_merged_config()

    def get_working_dir(self) -> Path:
        """Get the working directory, creating it if needed."""
        if self.config.paths.working_dir is not None:
            work_dir = self.config.paths.working_dir
        else:
            # Use platform-appropriate temp directory
            import tempfile

            work_dir = Path(tempfile.gettempdir()) / 'instagiffer'

        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def get_output_path(self, ensure_dir: bool = True) -> Path:
        """Get the output path, optionally ensuring directory exists."""
        output_path = self.config.paths.gif_output_path

        if ensure_dir:
            output_path.parent.mkdir(parents=True, exist_ok=True)

        return output_path

    def get_next_output_path(self) -> Path:
        """
        Get next available output path if overwrite is disabled.

        Returns numbered version like insta_001.gif, insta_002.gif, etc.
        """
        base_path = self.get_output_path(ensure_dir=True)

        if self.config.settings.overwrite_gif or not base_path.exists():
            return base_path

        # Find next available number
        stem = base_path.stem
        suffix = base_path.suffix
        parent = base_path.parent

        counter = 1
        while True:
            new_path = parent / f'{stem}_{counter:03d}{suffix}'
            if not new_path.exists():
                return new_path
            counter += 1

            # Safety check
            if counter > 9999:
                raise RuntimeError(
                    'Too many output files! Clean up or enable overwrite.'
                )

    def get_fail_safe_dir(self) -> Path:
        """
        Get fail-safe directory for non-latin locales.

        Uses user's custom path if set, otherwise returns platform-specific default.
        Only creates the directory if it doesn't exist.

        Returns:
            Path to fail-safe directory
        """
        # If user has configured a custom fail-safe directory, use it
        if self.config.paths.fail_safe_dir is not None:
            fail_safe = self.config.paths.fail_safe_dir
        else:
            # Platform-specific defaults
            import sys
            import tempfile

            if sys.platform == 'win32':
                # Windows: Use C:/instagiffer_temp if C: exists, else temp
                c_drive = Path('C:/')
                if c_drive.exists():
                    fail_safe = c_drive / 'instagiffer_temp'
                else:
                    fail_safe = Path(tempfile.gettempdir()) / 'instagiffer_temp'
            else:
                # Mac/Linux: Use /tmp/instagiffer
                fail_safe = Path('/tmp/instagiffer')

        # Create directory if needed
        fail_safe.mkdir(parents=True, exist_ok=True)
        return fail_safe

    def get_user_overrides(self) -> dict[str, Any]:
        """Get dictionary of user's custom settings (what differs from defaults)."""
        return self.user_overrides.copy()

    def has_user_overrides(self) -> bool:
        """Check if user has any custom settings."""
        return bool(self.user_overrides)

    def export_full_config(self, path: Path | str) -> None:
        """Export complete configuration to a file (for backup/sharing)."""
        path = Path(path)
        data = self.config.model_dump(mode='json')

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f'Exported full config to {path}')
