# Class Selector Feature (H1-H6)

## Overview
Users can now select their default working class (H1-H6) which will be used throughout the tool for all validations and calculations according to Israeli Survey Regulations (Directive ג2, 2021).

## What Was Implemented

### 1. Settings Manager Enhancement
**File:** `geodetic_tool/config/settings_manager.py`

Added methods to manage the default class:
- `get_default_class()` - Returns the user's selected class (defaults to H3)
- `set_default_class(class_name)` - Saves the user's class selection
- Settings are persisted in `~/.geodetic_tool/settings.json`

### 2. Regulation Module Enhancement
**File:** `geodetic_tool/config/israel_survey_regulations.py`

Added convenience functions:
- `get_default_class()` - Get the user's default class setting
- `set_default_class(class_name)` - Set the user's default class
- `get_default_class_parameters()` - Get parameters for the default class
- Updated `calculate_new_tolerance()` to use default class when not specified

### 3. GUI Enhancement
**File:** `geodetic_tool/gui/app.py`

Added a class selector to the status bar:
- **Combo box** for selecting H1-H6 classes
- **Info button (ℹ️)** to display detailed class parameters
- **Real-time status updates** when class is changed
- **Persistent selection** - saved and loaded automatically

Location: Bottom-right of the main window, next to the status message.

### 4. Validator Enhancement
**File:** `geodetic_tool/validators/__init__.py`

Updated validators to use the default class:
- `LevelingValidator` now uses default class when no class is specified
- `BatchValidator` now uses default class when no class is specified
- All validation automatically uses the selected class parameters

## How to Use

### Setting the Default Class

**Method 1: GUI (Recommended)**
1. Open the Geodetic Tool
2. Look at the bottom-right corner of the window
3. Find "Active Class / דרגת דיוק:" selector
4. Select your desired class (H1-H6) from the dropdown
5. The selection is automatically saved

**Method 2: Programmatically**
```python
from geodetic_tool.config.israel_survey_regulations import set_default_class

# Set to H2
set_default_class("H2")
```

### Getting Class Information

**In the GUI:**
1. Select a class from the dropdown
2. Click the ℹ️ button next to the selector
3. A dialog will show all parameters for that class:
   - Tolerance formula
   - Distance limits
   - Sight distance limits
   - Measurement requirements
   - Special requirements

**Programmatically:**
```python
from geodetic_tool.config.israel_survey_regulations import (
    get_default_class,
    get_default_class_parameters
)

# Get current default class
current_class = get_default_class()  # Returns "H3" (default)

# Get full parameters
params = get_default_class_parameters()
print(f"Tolerance: ±{params.tolerance_coefficient} mm√L")
print(f"Max sight distance: {params.max_sight_distance_geometric_m} m")
```

### Using Default Class in Validations

The validators now automatically use the default class:

```python
from geodetic_tool.validators import LevelingValidator, BatchValidator

# Creates validator using user's default class setting
validator = LevelingValidator()

# Or explicitly specify a class (overrides default)
validator_h1 = LevelingValidator(leveling_class=1)

# Batch validation also uses default class
batch_validator = BatchValidator()
```

## Class Parameters Summary

| Class | Tolerance | Max Line (km) | Max Sight Geometric (m) | Required Method |
|-------|-----------|---------------|------------------------|----------------|
| H1    | ±3mm√L    | Unlimited     | 30                     | BFFB           |
| H2    | ±5mm√L    | 60            | 40                     | BFFB           |
| H3    | ±10mm√L   | 24            | 50                     | BFFB           |
| H4    | ±20mm√L   | 10            | 80                     | BF             |
| H5    | ±30mm√L   | 5             | 100                    | BF             |
| H6    | ±60mm√L   | 4             | 100                    | BF             |

## Settings File Location

Settings are stored in JSON format at:
- **Windows:** `C:\Users\<username>\.geodetic_tool\settings.json`
- **Linux/Mac:** `~/.geodetic_tool/settings.json`

Example settings file:
```json
{
  "version": "1.0",
  "format": "geodetic_tool_class_parameters",
  "default_class": "H3",
  "class_parameters": {
    "H1": { ... },
    "H2": { ... },
    ...
  }
}
```

## Benefits

1. **Consistency** - All operations use the same class parameters
2. **Convenience** - No need to specify class for each operation
3. **Persistence** - Selection is saved and restored between sessions
4. **Visibility** - Current class is always visible in the status bar
5. **Quick Reference** - Info button provides instant access to class parameters
6. **Compliance** - Ensures all measurements meet Israeli Survey standards

## Technical Notes

- Default class is H3 (third order) if not set
- Class changes are saved immediately to disk
- All existing code that doesn't specify a class will use the default
- Code that explicitly specifies a class continues to work unchanged
- The setting integrates with the existing class parameters customization feature
