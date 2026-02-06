#!/usr/bin/env python
"""
Example: VS Code-style layered configuration.

This demonstrates how the config system works with defaults and overrides.
"""

import json
import tempfile
from pathlib import Path

from instagiffer.config import ConfigManager


def main():
    """Demonstrate layered configuration."""
    print("=== VS Code-Style Configuration Demo ===\n")

    # Use temp directory for demo
    with tempfile.TemporaryDirectory() as tmpdir:
        user_config_path = Path(tmpdir) / "user_config.json"

        # 1. Create config manager (loads defaults + user overrides)
        print("1. Creating config manager...")
        config = ConfigManager(user_config_path)
        print(f"   Defaults loaded from: {config.defaults_path}")
        print(f"   User config path: {config.user_config_path}")
        print(f"   User has overrides: {config.has_user_overrides()}\n")

        # 2. Show current settings (all from defaults)
        print("2. Current settings (all from defaults.json):")
        print(f"   FPS: {config.config.video.fps}")
        print(f"   GIF optimize: {config.config.gif.optimize}")
        print(f"   Theme: {config.config.settings.theme}\n")

        # 3. Update some settings
        print("3. Updating FPS to 24 and disabling optimization...")
        config.update(video__fps=24, gif__optimize=False)
        print(f"   New FPS: {config.config.video.fps}")
        print(f"   New optimize: {config.config.gif.optimize}\n")

        # 4. Show what's in the user config file (ONLY overrides!)
        print("4. User config file contains ONLY changed settings:")
        if user_config_path.exists():
            with open(user_config_path) as f:
                user_data = json.load(f)
            print(f"   {json.dumps(user_data, indent=2)}\n")

        # 5. Show user overrides
        print("5. User's custom settings (differs from defaults):")
        overrides = config.get_user_overrides()
        print(f"   {json.dumps(overrides, indent=2)}\n")

        # 6. Update more settings
        print("6. Changing theme to 'dark'...")
        config.update(settings__theme="dark")
        print(f"   New theme: {config.config.settings.theme}\n")

        # 7. Show updated user config (still minimal!)
        print("7. Updated user config (still only overrides):")
        with open(user_config_path) as f:
            user_data = json.load(f)
        print(f"   {json.dumps(user_data, indent=2)}\n")

        # 8. Export full config
        export_path = Path(tmpdir) / "full_config.json"
        print("8. Exporting complete config (for backup/sharing)...")
        config.export_full_config(export_path)
        with open(export_path) as f:
            full_data = json.load(f)
        print(f"   Exported to: {export_path}")
        print(f"   Contains {len(json.dumps(full_data))} characters\n")

        # 9. Compare sizes
        user_size = len(json.dumps(user_data))
        full_size = len(json.dumps(full_data))
        print("9. File size comparison:")
        print(f"   User config (overrides only): {user_size} bytes")
        print(f"   Full config (all settings): {full_size} bytes")
        print(f"   Savings: {full_size - user_size} bytes ({100 * (full_size - user_size) / full_size:.1f}%)\n")

        # 10. Reset to defaults
        print("10. Resetting to defaults...")
        config.reset_to_defaults()
        print(f"    User config file exists: {user_config_path.exists()}")
        print(f"    FPS back to: {config.config.video.fps}")
        print(f"    Optimize back to: {config.config.gif.optimize}")
        print(f"    Theme back to: {config.config.settings.theme}\n")

        # 11. Show the power of layering
        print("11. Platform-Specific Paths:")
        fail_safe = config.get_fail_safe_dir()
        print(f"    Default fail-safe dir: {fail_safe}")
        print("    (Platform-specific, not in user config!)\n")

        print("12. The Power of Layering:")
        print("    ✓ defaults.json ships with app (read-only)")
        print("    ✓ User config contains ONLY their changes")
        print("    ✓ Platform-specific paths determined at runtime")
        print("    ✓ Easy to see what user customized")
        print("    ✓ Easy to reset individual settings")
        print("    ✓ Easy to share/backup configs")
        print("    ✓ Small user config files")

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    main()
