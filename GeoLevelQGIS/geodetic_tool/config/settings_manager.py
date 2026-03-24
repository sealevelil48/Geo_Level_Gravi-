"""
Settings Manager for Geodetic Tool

Manages persistent user-editable regulation parameters.
Stores settings in JSON format at: ~/.geodetic_tool/settings.json

Features:
- Save/load class parameters (H1-H6)
- Reset to Survey of Israel defaults
- JSON format for human readability and version control
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Default settings location
DEFAULT_SETTINGS_DIR = Path.home() / ".geodetic_tool"
SETTINGS_FILE = DEFAULT_SETTINGS_DIR / "settings.json"


class SettingsManager:
    """
    Manages persistent settings for regulation parameters.

    Settings are stored in JSON format at ~/.geodetic_tool/settings.json
    """

    def __init__(self, settings_file: Optional[Path] = None):
        """
        Initialize settings manager.

        Args:
            settings_file: Optional custom settings file path (defaults to ~/.geodetic_tool/settings.json)
        """
        self.settings_file = settings_file or SETTINGS_FILE
        self.settings_dir = self.settings_file.parent
        self._ensure_settings_dir()

    def _ensure_settings_dir(self):
        """Create settings directory if it doesn't exist."""
        try:
            self.settings_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Settings directory verified: {self.settings_dir}")
        except Exception as e:
            logger.error(f"Failed to create settings directory: {e}")

    def save_class_parameters(self, class_params_dict: Dict[str, Dict[str, Any]]) -> bool:
        """
        Save class parameters to JSON.

        Args:
            class_params_dict: Dictionary mapping class names to parameter dicts
                Example:
                {
                    "H1": {"tolerance_coefficient": 3.0, "max_line_length_km": None, ...},
                    "H2": {...},
                    ...
                }

        Returns:
            True if save successful, False otherwise
        """
        # Load existing settings to preserve default_class if it exists
        existing_settings = self._load_settings_file()

        settings = {
            "version": "1.0",
            "format": "geodetic_tool_class_parameters",
            "default_class": existing_settings.get("default_class", "H3"),  # Preserve or default to H3
            "class_parameters": class_params_dict
        }

        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            logger.info(f"Settings saved successfully to {self.settings_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False

    def _load_settings_file(self) -> Dict[str, Any]:
        """
        Internal method to load the entire settings file.

        Returns:
            Settings dictionary or empty dict if not found
        """
        if not self.settings_file.exists():
            return {}

        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def load_class_parameters(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Load class parameters from JSON.

        Returns:
            Dictionary of class parameters if file exists and is valid, None otherwise
        """
        if not self.settings_file.exists():
            logger.info("Settings file does not exist - using defaults")
            return None

        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)

            # Validate format
            if not isinstance(settings, dict) or "class_parameters" not in settings:
                logger.warning("Invalid settings file format - using defaults")
                return None

            logger.info(f"Settings loaded successfully from {self.settings_file}")
            return settings.get("class_parameters")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse settings file (invalid JSON): {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
            return None

    def reset_to_defaults(self) -> bool:
        """
        Delete settings file to revert to Survey of Israel defaults.

        Returns:
            True if reset successful (or file didn't exist), False otherwise
        """
        try:
            if self.settings_file.exists():
                self.settings_file.unlink()
                logger.info("Settings reset to defaults")
            return True
        except Exception as e:
            logger.error(f"Failed to reset settings: {e}")
            return False

    def backup_settings(self, backup_suffix: str = "backup") -> Optional[Path]:
        """
        Create a backup of current settings.

        Args:
            backup_suffix: Suffix for backup file (default: "backup")

        Returns:
            Path to backup file if successful, None otherwise
        """
        if not self.settings_file.exists():
            logger.warning("No settings file to backup")
            return None

        try:
            backup_file = self.settings_file.with_suffix(f".{backup_suffix}.json")
            with open(self.settings_file, 'r', encoding='utf-8') as src:
                with open(backup_file, 'w', encoding='utf-8') as dst:
                    dst.write(src.read())
            logger.info(f"Settings backed up to {backup_file}")
            return backup_file
        except Exception as e:
            logger.error(f"Failed to backup settings: {e}")
            return None

    def get_default_class(self) -> str:
        """
        Get the default leveling class setting.

        Returns:
            Default class name (H1-H6), defaults to H3 if not set
        """
        settings = self._load_settings_file()
        default_class = settings.get("default_class", "H3")

        # Validate that it's a valid class
        if default_class not in ["H1", "H2", "H3", "H4", "H5", "H6"]:
            logger.warning(f"Invalid default class '{default_class}', using H3")
            return "H3"

        return default_class

    def set_default_class(self, class_name: str) -> bool:
        """
        Set the default leveling class.

        Args:
            class_name: Class name (H1-H6)

        Returns:
            True if save successful, False otherwise
        """
        # Validate class name
        if class_name not in ["H1", "H2", "H3", "H4", "H5", "H6"]:
            logger.error(f"Invalid class name: {class_name}. Must be H1-H6.")
            return False

        # Load existing settings
        settings = self._load_settings_file()

        # Update default class
        settings["default_class"] = class_name

        # Ensure structure is preserved
        if "version" not in settings:
            settings["version"] = "1.0"
        if "format" not in settings:
            settings["format"] = "geodetic_tool_class_parameters"
        if "class_parameters" not in settings:
            settings["class_parameters"] = {}

        # Save
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            logger.info(f"Default class set to {class_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to save default class: {e}")
            return False

    def get_settings_info(self) -> Dict[str, Any]:
        """
        Get information about current settings.

        Returns:
            Dictionary with settings metadata
        """
        info = {
            "settings_file": str(self.settings_file),
            "settings_dir": str(self.settings_dir),
            "file_exists": self.settings_file.exists(),
            "using_defaults": not self.settings_file.exists(),
            "default_class": self.get_default_class()
        }

        if self.settings_file.exists():
            try:
                info["file_size_bytes"] = self.settings_file.stat().st_size
                info["last_modified"] = self.settings_file.stat().st_mtime
            except Exception as e:
                logger.warning(f"Failed to get file stats: {e}")

        return info


# Module-level singleton instance
_settings_manager_instance: Optional[SettingsManager] = None


def get_settings_manager() -> SettingsManager:
    """
    Get the global settings manager instance (singleton pattern).

    Returns:
        SettingsManager instance
    """
    global _settings_manager_instance
    if _settings_manager_instance is None:
        _settings_manager_instance = SettingsManager()
    return _settings_manager_instance
