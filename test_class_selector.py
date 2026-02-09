#!/usr/bin/env python3
"""
Test script for the Class Selector feature.

This script demonstrates and tests the new default class selection functionality.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from geodetic_tool.config.israel_survey_regulations import (
    get_default_class,
    set_default_class,
    get_default_class_parameters,
    get_class_parameters_by_name
)
from geodetic_tool.config.settings_manager import get_settings_manager
from geodetic_tool.validators import LevelingValidator


def test_default_class_setting():
    """Test setting and getting default class."""
    print("=" * 70)
    print("Test 1: Default Class Setting")
    print("=" * 70)

    # Get current default
    current = get_default_class()
    print(f"Current default class: {current}")

    # Set to H2
    print("\nSetting default class to H2...")
    success = set_default_class("H2")
    print(f"Success: {success}")

    # Verify it was set
    new_default = get_default_class()
    print(f"New default class: {new_default}")
    assert new_default == "H2", "Failed to set default class to H2"

    # Restore to H3
    print("\nRestoring default class to H3...")
    set_default_class("H3")
    print(f"Restored to: {get_default_class()}")

    print("\n[PASS] Test passed!\n")


def test_class_parameters():
    """Test getting class parameters."""
    print("=" * 70)
    print("Test 2: Class Parameters")
    print("=" * 70)

    for class_name in ["H1", "H2", "H3", "H4", "H5", "H6"]:
        params = get_class_parameters_by_name(class_name)
        print(f"\n{class_name}:")
        print(f"  Tolerance: +/-{params.tolerance_coefficient} mmsqrtL")
        print(f"  Max line length: {params.max_line_length_km or 'Unlimited'} km")
        print(f"  Max sight (geometric): {params.max_sight_distance_geometric_m} m")
        print(f"  Required method: {params.required_method}")

    print("\n[PASS] Test passed!\n")


def test_validator_with_default_class():
    """Test that validator uses default class."""
    print("=" * 70)
    print("Test 3: Validator Using Default Class")
    print("=" * 70)

    # Set default to H4
    print("Setting default class to H4...")
    set_default_class("H4")

    # Create validator without specifying class
    print("Creating validator without specifying class...")
    validator = LevelingValidator()

    print(f"Validator leveling_class: {validator.leveling_class}")
    print(f"Validator class_params: {validator.class_params.class_name}")

    assert validator.leveling_class == 4, "Validator should use H4"
    assert validator.class_params.class_name == "H4", "Class params should be H4"

    print("\n[PASS] Validator correctly uses default class!\n")

    # Restore to H3
    set_default_class("H3")


def test_tolerance_calculation():
    """Test tolerance calculation with default class."""
    print("=" * 70)
    print("Test 4: Tolerance Calculation")
    print("=" * 70)

    from geodetic_tool.config.israel_survey_regulations import calculate_new_tolerance

    # Test with different classes
    distance_m = 5000  # 5 km

    for class_name in ["H1", "H3", "H5"]:
        set_default_class(class_name)
        params = get_default_class_parameters()

        # Calculate using default class (no class specified)
        tolerance = calculate_new_tolerance(distance_m)

        print(f"\n{class_name} (Default):")
        print(f"  Distance: {distance_m/1000} km")
        print(f"  Coefficient: {params.tolerance_coefficient}")
        print(f"  Tolerance: {tolerance:.2f} mm")

        # Verify against manual calculation
        import math
        expected = params.tolerance_coefficient * math.sqrt(distance_m / 1000)
        assert abs(tolerance - expected) < 0.01, f"Tolerance calculation incorrect for {class_name}"

    print("\n[PASS] All tolerance calculations correct!\n")

    # Restore to H3
    set_default_class("H3")


def test_settings_persistence():
    """Test that settings are persisted."""
    print("=" * 70)
    print("Test 5: Settings Persistence")
    print("=" * 70)

    manager = get_settings_manager()
    info = manager.get_settings_info()

    print(f"Settings file: {info['settings_file']}")
    print(f"File exists: {info['file_exists']}")
    print(f"Using defaults: {info['using_defaults']}")
    print(f"Current default class: {info['default_class']}")

    # Set to H5 and verify it persists
    print("\nSetting default to H5...")
    set_default_class("H5")

    # Create new manager instance to test persistence
    from geodetic_tool.config.settings_manager import SettingsManager
    new_manager = SettingsManager()
    persisted_class = new_manager.get_default_class()

    print(f"Class after creating new manager: {persisted_class}")
    assert persisted_class == "H5", "Default class should persist"

    print("\n[PASS] Settings correctly persisted!\n")

    # Restore to H3
    set_default_class("H3")


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("CLASS SELECTOR FEATURE TEST SUITE")
    print("=" * 70 + "\n")

    try:
        test_default_class_setting()
        test_class_parameters()
        test_validator_with_default_class()
        test_tolerance_calculation()
        test_settings_persistence()

        print("=" * 70)
        print("ALL TESTS PASSED! [PASS]")
        print("=" * 70)
        print("\nThe class selector feature is working correctly!")
        print("You can now select H1-H6 classes in the GUI status bar.")

    except AssertionError as e:
        print(f"\n[FAIL] TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
