"""Tests for configuration management."""

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

import instagiffer.config
from instagiffer.config import ConfigManager, InstaConfig
from instagiffer.config.manager import DEFAULTS_NAME


def _get_defaults():
    with open(Path(instagiffer.config.__file__).parent / DEFAULTS_NAME) as file_obj:
        return json.load(file_obj)


class TestInstaConfig:
    """Test configuration schema."""

    def test_default_config_creation(self, tmp_path):
        """Test creating default configuration."""
        # Use ConfigManager which loads defaults.json
        config_path = tmp_path / 'config.json'
        manager = ConfigManager(config_path)
        config = manager.config

        assert config.video.fps == 15.0
        assert config.gif.max_file_size_mb == 1.0
        assert config.settings.overwrite_gif is True

    def test_config_validation_fps(self):
        """Test FPS validation."""
        # Valid FPS
        defaults = _get_defaults()
        defaults['video']['fps'] = 30
        defaults['paths']['gif_output_path'] = '~/output/test.gif'

        config = InstaConfig.model_validate(defaults)
        assert config.video.fps == 30

        # Invalid FPS (too low)
        defaults['video']['fps'] = 0
        with pytest.raises(ValidationError):
            InstaConfig.model_validate(defaults)

        # Invalid FPS (too high)
        defaults['video']['fps'] = 1337
        with pytest.raises(ValidationError):
            InstaConfig.model_validate(defaults)

    def test_config_validation_colors(self, tmp_path):
        """Test color count validation."""
        config_path = tmp_path / 'config.json'
        manager = ConfigManager(config_path)

        # Valid - update from defaults
        manager.update(gif__colors=128)
        assert manager.config.gif.colors == 128

        # Invalid (too high)
        with pytest.raises(ValidationError):
            # Try to create config with invalid colors
            merged = manager.defaults.copy()
            merged['gif']['colors'] = 300
            InstaConfig.model_validate(merged)

    def test_config_to_dict(self, tmp_path):
        """Test serialization to dict."""
        config_path = tmp_path / 'config.json'
        manager = ConfigManager(config_path)
        config = manager.config

        data = config.model_dump()

        assert isinstance(data, dict)
        assert 'video' in data
        assert 'gif' in data
        assert 'paths' in data

    def test_config_from_dict(self, tmp_path):
        """Test deserialization from dict."""
        config_path = tmp_path / 'config.json'
        manager = ConfigManager(config_path)

        # Update using the manager
        manager.update(
            video__fps=20, video__quality=90, gif__optimize=False, gif__colors=64
        )

        assert manager.config.video.fps == 20
        assert manager.config.video.quality == 90
        assert manager.config.gif.optimize is False
        assert manager.config.gif.colors == 64


class TestConfigManager:
    """Test configuration manager."""

    @pytest.fixture
    def temp_config_path(self):
        """Create temporary config file path."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            path = Path(f.name)

        yield path

        # Cleanup
        if path.exists():
            path.unlink()

    def test_config_manager_creation(self, temp_config_path):
        """Test creating config manager."""
        manager = ConfigManager(temp_config_path)

        assert manager.user_config_path == temp_config_path
        assert isinstance(manager.config, InstaConfig)
        assert temp_config_path.exists()

    def test_save_and_load(self, temp_config_path):
        """Test saving and loading configuration."""
        # Create and save config
        manager = ConfigManager(temp_config_path)
        manager.config.video.fps = 24
        manager.config.gif.optimize = False
        manager.save_user_config()

        # Load in new manager
        manager2 = ConfigManager(temp_config_path)

        assert manager2.config.video.fps == 24
        assert manager2.config.gif.optimize is False

    def test_load_malformed_json(self, temp_config_path):
        """Test handling of malformed JSON."""
        # Write malformed JSON
        with open(temp_config_path, 'w') as f:
            f.write('{ invalid json }')

        # Should fall back to defaults
        manager = ConfigManager(temp_config_path)
        assert isinstance(manager.config, InstaConfig)
        assert manager.config.video.fps == 15.0  # Default value

    def test_load_invalid_config(self, temp_config_path):
        """Test handling of invalid config values."""
        # Write JSON with invalid values
        data = {'video': {'fps': 999}}  # Invalid FPS

        with open(temp_config_path, 'w') as f:
            json.dump(data, f)

        # Should fall back to defaults
        manager = ConfigManager(temp_config_path)
        assert manager.config.video.fps == 15.0  # Default value

    def test_update_config(self, temp_config_path):
        """Test updating specific config values."""
        manager = ConfigManager(temp_config_path)

        manager.update(video__fps=25, gif__colors=128)

        assert manager.config.video.fps == 25
        assert manager.config.gif.colors == 128

        # Verify it was saved
        manager2 = ConfigManager(temp_config_path)
        assert manager2.config.video.fps == 25

    def test_reset_to_defaults(self, temp_config_path):
        """Test resetting config to defaults."""
        manager = ConfigManager(temp_config_path)

        # Modify config
        manager.config.video.fps = 30
        manager.save_user_config()

        # Reset
        manager.reset_to_defaults()

        assert manager.config.video.fps == 15.0  # Back to default

    def test_get_working_dir(self, temp_config_path):
        """Test getting working directory."""
        manager = ConfigManager(temp_config_path)

        work_dir = manager.get_working_dir()

        assert work_dir.exists()
        assert work_dir.is_dir()

    def test_get_next_output_path_with_overwrite(self, temp_config_path):
        """Test getting output path when overwrite is enabled."""
        manager = ConfigManager(temp_config_path)
        manager.config.settings.overwrite_gif = True

        # Should always return same path
        path1 = manager.get_next_output_path()
        path2 = manager.get_next_output_path()

        assert path1 == path2

    def test_get_next_output_path_without_overwrite(self, temp_config_path):
        """Test getting numbered output paths when overwrite is disabled."""
        manager = ConfigManager(temp_config_path)
        manager.config.settings.overwrite_gif = False

        with tempfile.TemporaryDirectory() as tmpdir:
            manager.config.paths.gif_output_path = Path(tmpdir) / 'test.gif'

            # First call
            path1 = manager.get_next_output_path()
            path1.touch()  # Create the file

            # Second call should return numbered version
            path2 = manager.get_next_output_path()

            assert path1 != path2
            assert path2.stem.endswith('_001')

    def test_auto_cleanup_when_returning_to_defaults(self, temp_config_path):
        """Test that user config auto-cleans when values return to defaults."""
        manager = ConfigManager(temp_config_path)

        # Change FPS to something non-default
        manager.update(video__fps=24)

        # User config should exist with override
        assert temp_config_path.exists()
        with open(temp_config_path) as f:
            data = json.load(f)
        assert 'video' in data
        assert data['video']['fps'] == 24

        # Change FPS back to default (15)
        manager.update(video__fps=15)

        # User config should now be empty or not exist!
        if temp_config_path.exists():
            with open(temp_config_path) as f:
                data = json.load(f)
            # Should have no video section, or it should be empty
            assert 'video' not in data or not data.get('video')

    def test_path_expansion_not_treated_as_override(self, temp_config_path):
        """Test that path expansion doesn't create fake overrides."""
        manager = ConfigManager(temp_config_path)

        # Don't change anything - just access the config
        _ = manager.config.paths.gif_output_path

        # Save (this happens on update)
        manager.save_user_config(full_config=False)

        # User config should be empty (path expansion shouldn't count as change)
        if temp_config_path.exists():
            with open(temp_config_path) as f:
                data = json.load(f)
            # gif_output_path shouldn't be in overrides just because ~ was expanded
            assert 'paths' not in data or 'gif_output_path' not in data.get('paths', {})

    def test_reload_config(self, temp_config_path):
        """Test reloading config from disk."""
        manager = ConfigManager(temp_config_path)
        manager.config.video.fps = 20
        manager.save_user_config()

        # Modify in memory
        manager.config.video.fps = 30

        # Reload from disk
        manager.reload()

        assert manager.config.video.fps == 20  # Back to saved value

    def test_config_json_format(self, temp_config_path):
        """Test that saved JSON is properly formatted."""
        manager = ConfigManager(temp_config_path)
        manager.save_user_config(full_config=True)

        # Read raw JSON
        with open(temp_config_path) as file_obj:
            content = file_obj.read()

        # Should be pretty-printed
        assert '\n' in content
        assert '  ' in content  # Indentation

        # Should be valid JSON
        data = json.loads(content)
        assert isinstance(data, dict)


class TestPathExpansion:
    """Test path expansion and environment variables."""

    def test_path_expansion_home(self):
        """Test expanding home directory."""
        defaults = _get_defaults()
        defaults['paths']['gif_output_path'] = '~/output/test.gif'
        defaults['paths']['fail_safe_dir'] = None

        config = InstaConfig.model_validate(defaults)
        # Should expand ~
        assert '~' not in str(config.paths.gif_output_path)

    def test_path_creation(self, tmp_path):
        """Test that output directory is created."""
        config_path = tmp_path / 'config.json'
        manager = ConfigManager(config_path)

        output_dir = tmp_path / 'output'
        manager.config.paths.gif_output_path = output_dir / 'test.gif'

        # This should create the directory
        path = manager.get_output_path(ensure_dir=True)
        assert path.parent == output_dir

        assert output_dir.exists()
        assert output_dir.is_dir()

    def test_fail_safe_dir_default(self, tmp_path):
        """Test platform-specific fail-safe directory."""
        config_path = tmp_path / 'config.json'
        manager = ConfigManager(config_path)

        # Should return platform-specific default
        fail_safe = manager.get_fail_safe_dir()

        assert fail_safe.exists()
        assert fail_safe.is_dir()

        # Should be different based on platform
        import sys

        if sys.platform == 'win32':
            assert 'instagiffer' in str(fail_safe).lower()
        else:
            assert fail_safe == Path('/tmp/instagiffer')

    def test_fail_safe_dir_custom(self, tmp_path):
        """Test custom fail-safe directory."""
        config_path = tmp_path / 'config.json'
        manager = ConfigManager(config_path)

        # Set custom fail-safe directory
        custom_dir = tmp_path / 'custom_failsafe'
        manager.config.paths.fail_safe_dir = custom_dir

        # Should use custom directory
        fail_safe = manager.get_fail_safe_dir()

        assert fail_safe == custom_dir
        assert fail_safe.exists()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
