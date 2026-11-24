"""
Geodetic Leveling Tool - GUI Application

Tkinter-based graphical interface for the geodetic leveling automation tool.
Includes Line Adjustment and Network Adjustment (LSA) features.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
from pathlib import Path
import sys
import threading
import math
from typing import Any, List, Optional, Dict, Tuple
from datetime import datetime
from dataclasses import dataclass

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..parsers.base_parser import create_parser, detect_file_format
from .window_utils import fit_to_screen
from ..parsers.trimble_parser import TrimbleParser
from ..parsers.leica_parser import LeicaParser
from ..validators import LevelingValidator, BatchValidator
from ..engine.loop_detector import LoopAnalyzer, detect_double_runs
from ..engine.line_adjustment import LineAdjuster
from ..engine.least_squares import LeastSquaresAdjuster, ConditionalAdjuster
from ..engine.ADJwarnings import SingularMatrixError, InsufficientObservationsError, IllConditionedMatrixWarning
from ..config.models import LevelingLine, Benchmark, AdjustmentResult
from ..config.settings import FileFormat, calculate_tolerance, is_benchmark
import warnings

DEFAULT_CLASS = "H3"         # project precision class of a new project
DEFAULT_METHOD = "BFFB"      # project measure method of a new project

# Matplotlib imports - gracefully handle if not available
try:
    import matplotlib
    matplotlib.use('TkAgg')  # Set backend before importing pyplot
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
    print("Info: matplotlib loaded successfully. Visualization features enabled.")
except Exception as e:
    MATPLOTLIB_AVAILABLE = False
    print(f"Warning: matplotlib not available. Visualization features will be disabled. Error: {e}")


class PointExclusionDialog(tk.Toplevel):
    """
    Dialog for excluding points across the entire project (Phase 4, Item 15).

    Automatically disables all lines that use a specified point.
    """

    def __init__(self, parent, all_lines: List[LevelingLine]):
        super().__init__(parent)
        self.title("Point Exclusion / ה exclusion נקודה")
        fit_to_screen(self, 800, 600)
        self.transient(parent)
        self.grab_set()

        self.all_lines = all_lines
        self.all_points = set()
        self.point_usage = {}  # point_id -> list of lines
        self.excluded_lines = []

        self._analyze_points()
        self._create_widgets()

    def _analyze_points(self):
        """Analyze all points and their usage across files."""
        for line in self.all_lines:
            # Collect start and end points
            if line.start_point:
                self.all_points.add(line.start_point)
                if line.start_point not in self.point_usage:
                    self.point_usage[line.start_point] = []
                self.point_usage[line.start_point].append(line)

            if line.end_point:
                self.all_points.add(line.end_point)
                if line.end_point not in self.point_usage:
                    self.point_usage[line.end_point] = []
                self.point_usage[line.end_point].append(line)

            # Collect intermediate turning points
            for setup in line.setups:
                for pt in [setup.from_point, setup.to_point]:
                    if pt:
                        self.all_points.add(pt)
                        if pt not in self.point_usage:
                            self.point_usage[pt] = []
                        if line not in self.point_usage[pt]:
                            self.point_usage[pt].append(line)

    def _create_widgets(self):
        """Create dialog widgets."""
        # Top frame: Title and instructions
        top_frame = ttk.Frame(self)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        ttk.Label(top_frame, text="Point Exclusion Manager / ניהול הדרה נקודות",
                 font=('Arial', 12, 'bold')).pack()
        ttk.Label(top_frame,
                 text="Automatically exclude all lines that use a specified point",
                 font=('Arial', 9)).pack()

        # Main content paned window
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Left: Points list
        left_frame = ttk.LabelFrame(paned, text=f"All Points ({len(self.all_points)} total)")
        paned.add(left_frame, weight=1)

        # Search box
        search_frame = ttk.Frame(left_frame)
        search_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace('w', self._filter_points)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # Points listbox
        points_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL)
        self.points_listbox = tk.Listbox(left_frame, yscrollcommand=points_scroll.set,
                                        font=('Courier', 9))
        points_scroll.config(command=self.points_listbox.yview)

        self.points_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        points_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=5)

        self.points_listbox.bind('<<ListboxSelect>>', self._on_point_select)

        # Populate points
        sorted_points = sorted(self.all_points)
        for point in sorted_points:
            usage_count = len(self.point_usage.get(point, []))
            self.points_listbox.insert(tk.END, f"{point} (used in {usage_count} line(s))")

        # Right: Point details and actions
        right_frame = ttk.LabelFrame(paned, text="Point Details / פרטי נקודה")
        paned.add(right_frame, weight=2)

        # Details text
        self.details_text = scrolledtext.ScrolledText(right_frame, font=('Consolas', 9),
                                                      wrap=tk.WORD, height=20)
        self.details_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Action buttons
        action_frame = ttk.Frame(right_frame)
        action_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

        ttk.Button(action_frame, text="Exclude Point / הדר נקודה",
                  command=self._exclude_point).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Include Point / כלול נקודה",
                  command=self._include_point).pack(side=tk.LEFT, padx=5)

        # Bottom frame: Actions
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

        ttk.Button(bottom_frame, text="Close / סגור",
                  command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.status_label = ttk.Label(bottom_frame, text=f"Total points: {len(self.all_points)}", foreground="blue")
        self.status_label.pack(side=tk.LEFT, padx=10)

    def _filter_points(self, *args):
        """Filter points list based on search text."""
        from ..engine.point_normalizer import point_query_rank
        query = self.search_var.get()
        self.points_listbox.delete(0, tk.END)

        # Same matching as the plugin's DB search: "117" = point number 117
        # (not "1/17"), "117/MSA" also finds "117MSA"; best matches first.
        ranked = []
        for point in self.all_points:
            rank = point_query_rank(query, point)
            if rank is not None:
                ranked.append((rank, len(point), point))
        ranked.sort()
        self.points_listbox.insert(tk.END, *[
            f"{point} (used in {len(self.point_usage.get(point, []))} line(s))"
            for _, _, point in ranked])

    def _on_point_select(self, event):
        """Handle point selection."""
        selection = self.points_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        selected_text = self.points_listbox.get(index)
        # Extract point ID (text before first space)
        point_id = selected_text.split(' ')[0]

        self._show_point_details(point_id)

    def _show_point_details(self, point_id: str):
        """Show detailed usage information for a point."""
        self.details_text.delete('1.0', tk.END)

        # Header
        self.details_text.insert(tk.END, "=" * 70 + "\n")
        self.details_text.insert(tk.END, f"POINT DETAILS: {point_id}\n")
        self.details_text.insert(tk.END, "=" * 70 + "\n\n")

        # Usage statistics
        lines_using_point = self.point_usage.get(point_id, [])
        used_lines = [line for line in lines_using_point if line.is_used]
        excluded_lines = [line for line in lines_using_point if not line.is_used]

        self.details_text.insert(tk.END, f"Total Lines Using Point: {len(lines_using_point)}\n")
        self.details_text.insert(tk.END, f"  • Currently Used: {len(used_lines)}\n")
        self.details_text.insert(tk.END, f"  • Currently Excluded: {len(excluded_lines)}\n\n")

        # List files
        self.details_text.insert(tk.END, "FILES USING THIS POINT:\n")
        self.details_text.insert(tk.END, "-" * 70 + "\n\n")

        for i, line in enumerate(lines_using_point, 1):
            status = "✓ USED" if line.is_used else "✗ EXCLUDED"
            self.details_text.insert(tk.END, f"{i}. {status}: {line.filename or 'Unknown'}\n")
            self.details_text.insert(tk.END, f"   {line.start_point} → {line.end_point}\n")
            self.details_text.insert(tk.END, f"   Distance: {line.total_distance:.2f} m, Setups: {len(line.setups)}\n\n")

        # Action help
        self.details_text.insert(tk.END, "=" * 70 + "\n")
        self.details_text.insert(tk.END, "ACTIONS:\n")
        self.details_text.insert(tk.END, "=" * 70 + "\n\n")
        self.details_text.insert(tk.END, "• Exclude Point: Mark all lines using this point as excluded\n")
        self.details_text.insert(tk.END, "• Include Point: Mark all lines using this point as used\n")

    def _exclude_point(self):
        """Exclude all lines that use the selected point (Item 15)."""
        selection = self.points_listbox.curselection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a point first")
            return

        index = selection[0]
        selected_text = self.points_listbox.get(index)
        point_id = selected_text.split(' ')[0]

        lines_to_exclude = self.point_usage.get(point_id, [])
        used_lines = [line for line in lines_to_exclude if line.is_used]

        if not used_lines:
            messagebox.showinfo("Nothing to Exclude",
                f"All lines using point '{point_id}' are already excluded.")
            return

        # Confirm action
        confirm_msg = (f"Exclude all lines using point '{point_id}'?\n\n"
                      f"This will exclude {len(used_lines)} line(s):\n")
        for line in used_lines[:5]:
            confirm_msg += f"  • {line.filename}\n"
        if len(used_lines) > 5:
            confirm_msg += f"  ... and {len(used_lines) - 5} more\n"

        if not messagebox.askyesno("Confirm Exclusion", confirm_msg):
            return

        # Exclude lines
        for line in used_lines:
            line.is_used = False

        self.excluded_lines.extend(used_lines)

        messagebox.showinfo("Point Excluded",
            f"Excluded {len(used_lines)} line(s) using point '{point_id}'.\n\n"
            f"Lines marked as excluded.")

        # Refresh display
        self._show_point_details(point_id)
        self.status_label.config(text=f"Excluded {len(used_lines)} line(s)")

    def _include_point(self):
        """Include all lines that use the selected point."""
        selection = self.points_listbox.curselection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a point first")
            return

        index = selection[0]
        selected_text = self.points_listbox.get(index)
        point_id = selected_text.split(' ')[0]

        lines_to_include = self.point_usage.get(point_id, [])
        excluded_lines = [line for line in lines_to_include if not line.is_used]

        if not excluded_lines:
            messagebox.showinfo("Nothing to Include",
                f"All lines using point '{point_id}' are already included.")
            return

        # Confirm action
        confirm_msg = (f"Include all lines using point '{point_id}'?\n\n"
                      f"This will include {len(excluded_lines)} line(s).")

        if not messagebox.askyesno("Confirm Inclusion", confirm_msg):
            return

        # Include lines
        for line in excluded_lines:
            line.is_used = True

        messagebox.showinfo("Point Included",
            f"Included {len(excluded_lines)} line(s) using point '{point_id}'.")

        # Refresh display
        self._show_point_details(point_id)
        self.status_label.config(text=f"Included {len(excluded_lines)} line(s)")


class ClassSettingsDialog(tk.Toplevel):
    """
    Dialog for viewing and editing Survey of Israel class parameters (Phase 4, Item 4).

    Displays H1-H6 regulation parameters in a visual, editable format.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Class Settings / הגדרות דרגות דיוק")
        fit_to_screen(self, 1100, 800)
        self.transient(parent)
        self.grab_set()

        self.modified = False
        self.param_entries = {}  # Store entry widgets for editing

        self._create_widgets()
        self._load_parameters()

    def _create_widgets(self):
        """Create dialog widgets."""
        # Top frame: Title and instructions
        top_frame = ttk.Frame(self)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        ttk.Label(top_frame, text="Survey of Israel Leveling Class Parameters",
                 font=('Arial', 12, 'bold')).pack()
        ttk.Label(top_frame,
                 text="Based on Directive ג2 (06/06/2021) - Orthometric Height Measurement",
                 font=('Arial', 9)).pack()

        # Create notebook for class tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Create a tab for each class
        self.class_frames = {}
        for class_num in range(1, 7):
            frame = self._create_class_tab(class_num)
            self.class_frames[class_num] = frame
            self.notebook.add(frame, text=f"H{class_num}")

        # Bottom frame: Actions
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

        ttk.Button(bottom_frame, text="Save Changes / שמור שינויים",
                  command=self._save_changes).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_frame, text="Reset to Defaults / אפס לברירות מחדל",
                  command=self._reset_defaults).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_frame, text="Close / סגור",
                  command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.status_label = ttk.Label(bottom_frame, text="", foreground="blue")
        self.status_label.pack(side=tk.LEFT, padx=10)

    def _create_class_tab(self, class_num: int) -> ttk.Frame:
        """Create parameter display tab for a specific class."""
        frame = ttk.Frame(self.notebook)

        # Create scrolled frame
        canvas = tk.Canvas(frame)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Store reference to scrollable frame
        frame.scrollable_frame = scrollable_frame

        return frame

    def _load_parameters(self):
        """Load and display parameters for all classes."""
        from ..config.israel_survey_regulations import CLASS_REGISTRY

        for class_num, params in CLASS_REGISTRY.items():
            frame = self.class_frames[class_num].scrollable_frame

            # Class header
            header_frame = ttk.LabelFrame(frame, text=f"Class {params.class_name} Parameters")
            header_frame.pack(fill=tk.X, padx=10, pady=10)

            # Tolerance formula (editable)
            ttk.Label(header_frame, text="Tolerance Coefficient (mm×√km):",
                     font=('Arial', 10, 'bold')).grid(row=0, column=0, sticky=tk.W, padx=10, pady=5)
            tolerance_var = tk.DoubleVar(value=params.tolerance_coefficient)
            tolerance_entry = ttk.Entry(header_frame, textvariable=tolerance_var, width=15)
            tolerance_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
            self.param_entries[f"H{class_num}_tolerance"] = (tolerance_var, 'tolerance_coefficient')
            tolerance_entry.bind('<KeyRelease>', lambda e: setattr(self, 'modified', True))

            # Distance limits (editable)
            ttk.Label(header_frame, text="Max Line Length (km, 0=unlimited):",
                     font=('Arial', 10, 'bold')).grid(row=1, column=0, sticky=tk.W, padx=10, pady=5)
            max_length_val = params.max_line_length_km if params.max_line_length_km else 0
            max_length_var = tk.DoubleVar(value=max_length_val)
            max_length_entry = ttk.Entry(header_frame, textvariable=max_length_var, width=15)
            max_length_entry.grid(row=1, column=1, sticky=tk.W, pady=5)
            self.param_entries[f"H{class_num}_max_length"] = (max_length_var, 'max_line_length_km')
            max_length_entry.bind('<KeyRelease>', lambda e: setattr(self, 'modified', True))

            # Sight distances (editable)
            sight_frame = ttk.LabelFrame(frame, text="Sight Distance Limits (meters)")
            sight_frame.pack(fill=tk.X, padx=10, pady=10)

            ttk.Label(sight_frame, text="Geometric Leveling (m):",
                     font=('Arial', 10, 'bold')).grid(row=0, column=0, sticky=tk.W, padx=10, pady=5)
            sight_geom_var = tk.DoubleVar(value=params.max_sight_distance_geometric_m)
            sight_geom_entry = ttk.Entry(sight_frame, textvariable=sight_geom_var, width=15)
            sight_geom_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
            self.param_entries[f"H{class_num}_sight_geom"] = (sight_geom_var, 'max_sight_distance_geometric_m')
            sight_geom_entry.bind('<KeyRelease>', lambda e: setattr(self, 'modified', True))

            ttk.Label(sight_frame, text="Trigonometric Leveling (m):",
                     font=('Arial', 10, 'bold')).grid(row=1, column=0, sticky=tk.W, padx=10, pady=5)
            sight_trig_var = tk.DoubleVar(value=params.max_sight_distance_trigonometric_m)
            sight_trig_entry = ttk.Entry(sight_frame, textvariable=sight_trig_var, width=15)
            sight_trig_entry.grid(row=1, column=1, sticky=tk.W, pady=5)
            self.param_entries[f"H{class_num}_sight_trig"] = (sight_trig_var, 'max_sight_distance_trigonometric_m')
            sight_trig_entry.bind('<KeyRelease>', lambda e: setattr(self, 'modified', True))

            # Measurement method
            method_frame = ttk.LabelFrame(frame, text="Measurement Requirements")
            method_frame.pack(fill=tk.X, padx=10, pady=10)

            ttk.Label(method_frame, text="Required Method:",
                     font=('Arial', 10, 'bold')).grid(row=0, column=0, sticky=tk.W, padx=10, pady=5)
            method_desc = "BFFB (Back-Fore-Fore-Back)" if params.required_method == "BFFB" else "BF (Back-Fore)"
            ttk.Label(method_frame, text=method_desc).grid(row=0, column=1, sticky=tk.W, pady=5)

            ttk.Label(method_frame, text="Double-Run Required:",
                     font=('Arial', 10, 'bold')).grid(row=1, column=0, sticky=tk.W, padx=10, pady=5)
            ttk.Label(method_frame, text="Yes / כן" if params.requires_double_run else "No / לא").grid(row=1, column=1, sticky=tk.W, pady=5)

            # Distance balance (editable)
            balance_frame = ttk.LabelFrame(frame, text="Distance Balance Requirements (meters)")
            balance_frame.pack(fill=tk.X, padx=10, pady=10)

            ttk.Label(balance_frame, text="Max Single Setup Imbalance (m):",
                     font=('Arial', 10, 'bold')).grid(row=0, column=0, sticky=tk.W, padx=10, pady=5)
            single_imb_var = tk.DoubleVar(value=params.max_single_distance_imbalance_m)
            single_imb_entry = ttk.Entry(balance_frame, textvariable=single_imb_var, width=15)
            single_imb_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
            self.param_entries[f"H{class_num}_single_imb"] = (single_imb_var, 'max_single_distance_imbalance_m')
            single_imb_entry.bind('<KeyRelease>', lambda e: setattr(self, 'modified', True))

            ttk.Label(balance_frame, text="Max Cumulative Imbalance (m):",
                     font=('Arial', 10, 'bold')).grid(row=1, column=0, sticky=tk.W, padx=10, pady=5)
            cum_imb_var = tk.DoubleVar(value=params.max_cumulative_distance_imbalance_m)
            cum_imb_entry = ttk.Entry(balance_frame, textvariable=cum_imb_var, width=15)
            cum_imb_entry.grid(row=1, column=1, sticky=tk.W, pady=5)
            self.param_entries[f"H{class_num}_cum_imb"] = (cum_imb_var, 'max_cumulative_distance_imbalance_m')
            cum_imb_entry.bind('<KeyRelease>', lambda e: setattr(self, 'modified', True))

            # Special requirements
            special_frame = ttk.LabelFrame(frame, text="Special Requirements")
            special_frame.pack(fill=tk.X, padx=10, pady=10)

            row = 0
            if params.requires_invar_staff:
                ttk.Label(special_frame, text="• Invar Staff Required (אמה עשויה אינוור)",
                         font=('Arial', 9)).grid(row=row, column=0, sticky=tk.W, padx=10, pady=2)
                row += 1

            if params.requires_staff_supports:
                ttk.Label(special_frame, text="• Staff Supports Required (מוטות משען לייצוב האמות)",
                         font=('Arial', 9)).grid(row=row, column=0, sticky=tk.W, padx=10, pady=2)
                row += 1

            if params.requires_calibration_monthly:
                ttk.Label(special_frame, text="• Monthly Calibration Required",
                         font=('Arial', 9)).grid(row=row, column=0, sticky=tk.W, padx=10, pady=2)
                row += 1

            if params.requires_orthometric_correction:
                ttk.Label(special_frame, text="• Orthometric Correction Required (gravity-based)",
                         font=('Arial', 9)).grid(row=row, column=0, sticky=tk.W, padx=10, pady=2)
                row += 1

            if params.max_instrument_error_mm_per_km:
                ttk.Label(special_frame, text=f"• Max Instrument Error: {params.max_instrument_error_mm_per_km} mm/km",
                         font=('Arial', 9)).grid(row=row, column=0, sticky=tk.W, padx=10, pady=2)
                row += 1

            if params.max_days_for_double_run:
                ttk.Label(special_frame, text=f"• Complete Double-Run Within: {params.max_days_for_double_run} days",
                         font=('Arial', 9)).grid(row=row, column=0, sticky=tk.W, padx=10, pady=2)
                row += 1

            if row == 0:
                ttk.Label(special_frame, text="No special requirements",
                         font=('Arial', 9, 'italic')).grid(row=0, column=0, sticky=tk.W, padx=10, pady=5)

        self.status_label.config(text="Loaded parameters from regulations")

    def _save_changes(self):
        """Save modified parameters to settings file (Item 5)."""
        from ..config import israel_survey_regulations
        from ..config.israel_survey_regulations import CLASS_REGISTRY

        if not self.modified:
            messagebox.showinfo("No Changes", "No changes to save.")
            return

        # Validate and apply changes to CLASS_REGISTRY
        try:
            for key, (var, attr_name) in self.param_entries.items():
                # Extract class number from key (e.g., "H3_tolerance" -> 3)
                class_num = int(key.split('_')[0][1:])
                value = var.get()

                # Validate value
                if value < 0:
                    raise ValueError(f"Negative value not allowed for {attr_name}")

                # Special handling for max_line_length_km (0 means None)
                if attr_name == 'max_line_length_km' and value == 0:
                    value = None

                # Update CLASS_REGISTRY
                setattr(CLASS_REGISTRY[class_num], attr_name, value)

            # Save current parameters to settings file
            success = israel_survey_regulations.save_user_settings()

        except ValueError as e:
            messagebox.showerror("Validation Error", f"Invalid parameter value:\n{str(e)}")
            return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply changes:\n{str(e)}")
            return

        if success:
            self.status_label.config(text="✓ Settings saved successfully", foreground="green")
            messagebox.showinfo("Success",
                "Settings saved to:\n~/.geodetic_tool/settings.json\n\n"
                "Changes will be applied on next application start.")
        else:
            self.status_label.config(text="✗ Failed to save settings", foreground="red")
            messagebox.showerror("Error", "Failed to save settings file")

    def _reset_defaults(self):
        """Reset to default Survey of Israel regulation parameters (Item 5)."""
        from ..config import israel_survey_regulations

        if not messagebox.askyesno("Reset Defaults",
                                  "Reset all parameters to Survey of Israel defaults?\n\n"
                                  "This will delete your custom settings file and\n"
                                  "revert to official Directive ג2 (2021) specifications.\n\n"
                                  "Continue?"):
            return

        # Reset settings file
        success = israel_survey_regulations.reset_to_defaults()

        if success:
            # Reload parameters from defaults
            israel_survey_regulations.load_user_settings()

            # Clear and reload UI
            for frame in self.class_frames.values():
                for widget in frame.scrollable_frame.winfo_children():
                    widget.destroy()

            self._load_parameters()

            self.status_label.config(text="✓ Reset to Survey of Israel defaults", foreground="green")
            messagebox.showinfo("Reset Complete",
                "Parameters reset to official Survey of Israel defaults.\n\n"
                "Directive ג2 (06/06/2021) specifications restored.")
        else:
            self.status_label.config(text="✗ Failed to reset settings", foreground="red")
            messagebox.showerror("Error", "Failed to reset settings")


class MergeDialog(tk.Toplevel):
    """
    Dialog for merging leveling line segments (Phase 3, Items 5, 12/13, 14).

    Features:
    - Detects mergeable line segments
    - Shows smart vector reversal requirements
    - Displays common nodes and merge preview
    - Applies merge with state management
    """

    def __init__(self, parent, all_lines: List[LevelingLine], selected_indices: List[int] = None):
        super().__init__(parent)
        self.title("Merge Line Segments / מיזוג קווים")
        fit_to_screen(self, 1000, 700)
        self.transient(parent)
        self.grab_set()

        self.all_lines = all_lines
        self.selected_indices = selected_indices
        self.coordinator = None
        self.candidates = []
        self.selected_candidate = None
        self.merged_line = None

        self._create_widgets()
        self._find_candidates()

    def _create_widgets(self):
        """Create dialog widgets."""
        # Top frame: Instructions
        top_frame = ttk.Frame(self)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        ttk.Label(top_frame, text="Line Merge Wizard / אשף מיזוג קווים",
                 font=('Arial', 12, 'bold')).pack()
        ttk.Label(top_frame,
                 text="Intelligently merge line segments with automatic direction alignment",
                 font=('Arial', 9)).pack()

        # Main content paned window
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Left: Candidates list
        left_frame = ttk.LabelFrame(paned, text="Merge Candidates / אפשרויות מיזוג")
        paned.add(left_frame, weight=1)

        # Candidates listbox
        candidates_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL)
        self.candidates_listbox = tk.Listbox(left_frame, yscrollcommand=candidates_scroll.set,
                                            font=('Courier', 9))
        candidates_scroll.config(command=self.candidates_listbox.yview)

        self.candidates_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        candidates_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=5)

        self.candidates_listbox.bind('<<ListboxSelect>>', self._on_candidate_select)

        # Right: Merge preview
        right_frame = ttk.LabelFrame(paned, text="Merge Preview / תצוגה מקדימה")
        paned.add(right_frame, weight=2)

        # Preview text
        self.preview_text = scrolledtext.ScrolledText(right_frame, font=('Consolas', 9),
                                                      wrap=tk.WORD, height=30)
        self.preview_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Bottom frame: Actions
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

        ttk.Button(bottom_frame, text="Apply Merge / בצע מיזוג",
                  command=self._apply_merge).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_frame, text="Cancel / ביטול",
                  command=self.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_frame, text="Refresh / רענן",
                  command=self._find_candidates).pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(bottom_frame, text="", foreground="blue")
        self.status_label.pack(side=tk.LEFT, padx=20)

    def _find_candidates(self):
        """Find merge candidates using LineCoordinator."""
        from ..engine.line_coordinator import LineCoordinator

        self.candidates_listbox.delete(0, tk.END)
        self.preview_text.delete('1.0', tk.END)

        # Initialize coordinator
        if self.selected_indices:
            lines_to_check = [self.all_lines[i] for i in self.selected_indices
                            if i < len(self.all_lines)]
            self.coordinator = LineCoordinator(lines_to_check)
        else:
            self.coordinator = LineCoordinator(self.all_lines)

        # Find candidates
        self.candidates = self.coordinator.find_merge_candidates()

        if not self.candidates:
            self.candidates_listbox.insert(tk.END, "No merge candidates found.")
            self.preview_text.insert(tk.END,
                "No mergeable line segments detected.\n\n"
                "Lines can be merged if they:\n"
                "• Share common endpoints (turning points)\n"
                "• Form a continuous path\n"
                "• Are marked as 'used' (not excluded)\n\n"
                "Tip: Select specific lines first, then try merge.")
            self.status_label.config(text="No candidates found")
            return

        # Display candidates
        for i, candidate in enumerate(self.candidates):
            summary = self.coordinator.get_merge_summary(candidate)
            display_text = (f"[{i+1}] {summary['start_point']} → {summary['end_point']} "
                          f"({summary['num_segments']} segments, "
                          f"{summary['total_distance']:.1f}m, "
                          f"{summary['total_setups']} setups)")
            self.candidates_listbox.insert(tk.END, display_text)

        self.status_label.config(text=f"Found {len(self.candidates)} candidate(s)")

        # Auto-select first candidate
        if self.candidates:
            self.candidates_listbox.selection_set(0)
            self._on_candidate_select(None)

    def _on_candidate_select(self, event):
        """Handle candidate selection."""
        selection = self.candidates_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        if index >= len(self.candidates):
            return

        self.selected_candidate = self.candidates[index]
        self._show_preview()

    def _show_preview(self):
        """Show detailed preview of selected merge candidate."""
        if not self.selected_candidate:
            return

        self.preview_text.delete('1.0', tk.END)

        summary = self.coordinator.get_merge_summary(self.selected_candidate)

        # Header
        self.preview_text.insert(tk.END, "=" * 70 + "\n")
        self.preview_text.insert(tk.END, "MERGE PREVIEW / תצוגה מקדימה\n")
        self.preview_text.insert(tk.END, "=" * 70 + "\n\n")

        # Summary
        self.preview_text.insert(tk.END, f"Merged Line: {summary['start_point']} → {summary['end_point']}\n")
        self.preview_text.insert(tk.END, f"Total Distance: {summary['total_distance']:.2f} m "
                                        f"({summary['total_distance']/1000:.3f} km)\n")
        self.preview_text.insert(tk.END, f"Total Setups: {summary['total_setups']}\n")
        self.preview_text.insert(tk.END, f"Number of Segments: {summary['num_segments']}\n\n")

        # Common nodes
        if summary['common_nodes']:
            self.preview_text.insert(tk.END, f"Common Nodes (PKT): {', '.join(summary['common_nodes'])}\n\n")

        # Segments detail
        self.preview_text.insert(tk.END, "SEGMENTS:\n")
        self.preview_text.insert(tk.END, "-" * 70 + "\n\n")

        for i, seg in enumerate(summary['segments'], 1):
            self.preview_text.insert(tk.END, f"Segment {i}:\n")
            self.preview_text.insert(tk.END, f"  File: {seg['filename']}\n")
            self.preview_text.insert(tk.END, f"  Direction: {seg['direction']}\n")

            if seg['needs_reversal']:
                self.preview_text.insert(tk.END, f"  ⚠ REVERSAL REQUIRED (direction will be flipped)\n")
            else:
                self.preview_text.insert(tk.END, f"  ✓ Direction OK (no reversal needed)\n")

            self.preview_text.insert(tk.END, f"  Distance: {seg['distance']:.2f} m\n")
            self.preview_text.insert(tk.END, f"  Setups: {seg['setups']}\n\n")

        # Warnings
        self.preview_text.insert(tk.END, "=" * 70 + "\n")
        self.preview_text.insert(tk.END, "APPLY MERGE ACTION:\n")
        self.preview_text.insert(tk.END, "=" * 70 + "\n\n")
        self.preview_text.insert(tk.END, "When you click 'Apply Merge':\n")
        self.preview_text.insert(tk.END, f"• New merged line will be created: MERGED_{summary['start_point']}-{summary['end_point']}\n")
        self.preview_text.insert(tk.END, f"• Original {summary['num_segments']} segment(s) will be marked as EXCLUDED\n")
        self.preview_text.insert(tk.END, "• Reversals will be applied automatically where needed\n")
        self.preview_text.insert(tk.END, "• Setups will be renumbered sequentially\n")

    def _apply_merge(self):
        """Apply the selected merge (Item 14: State management)."""
        if not self.selected_candidate:
            messagebox.showwarning("No Selection", "Please select a merge candidate first")
            return

        # Confirm action
        summary = self.coordinator.get_merge_summary(self.selected_candidate)
        confirm_msg = (f"Apply merge?\n\n"
                      f"Merged line: {summary['start_point']} → {summary['end_point']}\n"
                      f"Segments: {summary['num_segments']}\n"
                      f"Total distance: {summary['total_distance']:.2f} m\n\n"
                      f"Original segments will be excluded.")

        if not messagebox.askyesno("Confirm Merge", confirm_msg):
            return

        # Apply merge with state management
        try:
            self.merged_line = self.coordinator.apply_merge(
                self.selected_candidate,
                self.all_lines,
                merged_filename=None  # Auto-generate
            )

            messagebox.showinfo("Merge Complete",
                f"Successfully merged {summary['num_segments']} segments.\n\n"
                f"New line: {self.merged_line.filename}\n"
                f"Start: {self.merged_line.start_point}\n"
                f"End: {self.merged_line.end_point}\n"
                f"Distance: {self.merged_line.total_distance:.2f} m\n"
                f"Setups: {len(self.merged_line.setups)}\n\n"
                f"Original segments marked as excluded.")

            self.destroy()

        except Exception as e:
            messagebox.showerror("Merge Error", f"Failed to apply merge:\n{str(e)}")


class GeodeticToolGUI:
    """Main GUI application for the Geodetic Leveling Tool."""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Geodetic Leveling Tool - פילוס גיאודטי")
        fit_to_screen(self.root, 1200, 800)
        self.root.minsize(800, 600)

        # Data storage
        self.lines: List[LevelingLine] = []
        self.file_paths: List[str] = []
        # Known heights read from .FA0 / .FA1 files {point_id: height}
        self.file_benchmarks: Dict[str, float] = {}
        # Approximate heights listed in .FA0 / .FA1 files (not fixed)
        self.file_approx: Dict[str, float] = {}
        # Benchmark DB: EPSG:2039 coordinates of the project's points
        # {point_id: PointCoordinate} and the Point Verification state
        self.point_coords: Dict[str, Any] = {}
        self.point_overrides: Dict[str, dict] = {}
        self.point_origin: Dict[str, str] = {}

        # Settings
        from ..config.settings import get_settings
        self.settings = get_settings()

        # NEW: Session tracking for removed/excluded files (Item 16)
        self.removed_files_log: List[Dict[str, Any]] = []
        from datetime import datetime
        self.session_start_time = datetime.now()

        # NEW: Project management
        from ..config.models import ProjectData
        from ..config.project_manager import ProjectManager
        self.current_project: Optional[ProjectData] = ProjectData(name="Unnamed Project")
        self.project_manager = ProjectManager()
        # One measure method for every line (Precision Class panel)
        self.project_method = DEFAULT_METHOD
        # Project folder <parent>/<name>/ holding every file of the project
        from ..project_log import ProjectLogFile
        self.project_dir = ""
        self.last_parent_dir = ""
        self.project_log = ProjectLogFile()

        # Create the GUI
        self._create_menu()
        self._create_main_layout()
        self._create_status_bar()
        self._install_log_capture()
        self._sync_default_class()

        # Configure styles
        self._configure_styles()
    
    def _configure_styles(self):
        """Configure ttk styles."""
        style = ttk.Style()
        style.configure('Valid.TLabel', foreground='green')
        style.configure('Invalid.TLabel', foreground='red')
        style.configure('Header.TLabel', font=('Arial', 12, 'bold'))
    
    def _create_menu(self):
        """Create the menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File / קובץ", menu=file_menu)
        file_menu.add_command(label="Open Files... / פתח קבצים", command=self._open_files, accelerator="Ctrl+O")
        file_menu.add_command(label="Open Folder... / פתח תיקייה", command=self._open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Export Results... / ייצוא תוצאות", command=self._export_results)
        file_menu.add_command(label="Export to QGIS... / ייצוא ל-QGIS", command=self._export_qgis)
        file_menu.add_separator()
        file_menu.add_command(label="View Removed Files Report... / דוח קבצים שהוסרו", command=self._view_removed_files_report)
        file_menu.add_separator()
        file_menu.add_command(label="Exit / יציאה", command=self.root.quit, accelerator="Alt+F4")

        # NEW: Project menu
        project_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Project / פרויקט", menu=project_menu)
        project_menu.add_command(label="Save Project... / שמור פרויקט", command=self._save_project)
        project_menu.add_command(label="Load Project... / טען פרויקט", command=self._load_project)
        project_menu.add_command(label="Export Project as ZIP... / ייצוא פרויקט כ-ZIP",
                                 command=self._export_project_zip)
        project_menu.add_command(label="Export Log... / ייצוא יומן", command=self._export_log)
        project_menu.add_separator()
        project_menu.add_command(label="Create Joint Project... / צור פרויקט משולב", command=self._create_joint_project)
        project_menu.add_separator()
        project_menu.add_command(label="Project Properties... / מאפייני פרויקט", command=self._show_project_properties)
        
        # Analysis menu
        analysis_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Analysis / ניתוח", menu=analysis_menu)
        analysis_menu.add_command(label="Validate All / בדיקת תקינות", command=self._validate_all)
        analysis_menu.add_command(label="Detect Double-Runs / זיהוי הלוך-שוב", command=self._detect_double_runs)
        analysis_menu.add_command(label="Find Loops / חיפוש לולאות", command=self._find_loops)
        analysis_menu.add_separator()
        analysis_menu.add_command(label="Line Adjustment (selected line) / תיאום קו",
                                 command=self._line_adjustment)
        analysis_menu.add_command(label="Network Adjustment (LSA) / תיאום רשת",
                                 command=self._network_adjustment, accelerator="Ctrl+Shift+N")
        analysis_menu.add_separator()
        analysis_menu.add_command(label="Merge Line Segments... / מיזוג קווים",
                                 command=self._merge_lines, accelerator="Ctrl+M")

        # Settings menu (Phase 4, Item 4, 15)
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings / הגדרות", menu=settings_menu)
        settings_menu.add_command(label="Class Parameters... / פרמטרי דרגות דיוק",
                                 command=self._show_class_settings)
        settings_menu.add_command(label="Encoding... / קידוד תווים",
                                 command=self._show_encoding_settings)
        settings_menu.add_command(label="Database Connection... / חיבור למסד נתונים",
                                 command=self._show_db_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="Point Exclusion... / הדרת נקודות",
                                 command=self._manage_point_exclusion)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help / עזרה", menu=help_menu)
        help_menu.add_command(label="Documentation / תיעוד", command=self._show_docs)
        help_menu.add_command(label="About / אודות", command=self._show_about)
        
        # Keyboard shortcuts
        self.root.bind('<Control-o>', lambda e: self._open_files())
        self.root.bind('<Control-Shift-N>', lambda e: self._network_adjustment())
        self.root.bind('<Control-m>', lambda e: self._merge_lines())
    
    def _create_main_layout(self):
        """Create the main layout with paned windows."""
        # Main paned window (horizontal)
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left panel - File list
        left_frame = ttk.Frame(main_paned, width=300)
        main_paned.add(left_frame, weight=1)
        
        self._create_file_panel(left_frame)
        
        # Right panel - Details and results
        right_frame = ttk.Frame(main_paned, width=700)
        main_paned.add(right_frame, weight=3)
        
        # Notebook for different views
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Tab 1: Line Details
        details_frame = ttk.Frame(self.notebook)
        self.notebook.add(details_frame, text="Line Details / פרטי קו")
        self._create_details_panel(details_frame)
        
        # Tab 2: Validation Results
        validation_frame = ttk.Frame(self.notebook)
        self.notebook.add(validation_frame, text="Validation / בדיקות")
        self._create_validation_panel(validation_frame)
        
        # Tab 3: Analysis Results
        analysis_frame = ttk.Frame(self.notebook)
        self.notebook.add(analysis_frame, text="Analysis / ניתוח")
        self._create_analysis_panel(analysis_frame)
        
        # Tab 4: Log
        log_frame = ttk.Frame(self.notebook)
        self.notebook.add(log_frame, text="Log / יומן")
        self._create_log_panel(log_frame)
    
    def _create_file_panel(self, parent: ttk.Frame):
        """Create the file list panel."""
        # Toolbar
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(0, 5))

        ttk.Button(toolbar, text="➕ Add", command=self._open_files).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="🗑️ Clear", command=self._clear_files).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="↻ Reload", command=self._reload_files).pack(side=tk.LEFT, padx=2)

        # NEW: Additional toolbar buttons
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=5, fill=tk.Y)
        ttk.Button(toolbar, text="⇄ Toggle Dir", command=self._toggle_line_direction).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="✓/✗ Toggle Use", command=self._toggle_line_used).pack(side=tk.LEFT, padx=2)

        # Precision Class: ONE class and ONE method for the whole project
        from ..validators import MEASURE_METHODS
        pc = ttk.LabelFrame(parent, text="Precision Class / דרגת דיוק")
        pc.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(pc, text="Class:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.class_selector_var = tk.StringVar(value=DEFAULT_CLASS)
        self.class_combo = ttk.Combobox(pc, textvariable=self.class_selector_var, state="readonly",
                                        values=["H1", "H2", "H3", "H4", "H5", "H6"], width=8, height=12)
        self.class_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        self.class_combo.bind("<<ComboboxSelected>>", self._on_class_changed)
        ttk.Button(pc, text="ℹ️", width=3, command=self._show_class_info).grid(row=0, column=2, padx=2)
        ttk.Label(pc, text="Method:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.method_var = tk.StringVar(value=DEFAULT_METHOD)
        self.method_combo = ttk.Combobox(pc, textvariable=self.method_var, state="readonly",
                                         values=list(MEASURE_METHODS), width=8, height=12)
        self.method_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        self.method_combo.bind("<<ComboboxSelected>>", self._on_method_changed)
        self.class_desc_var = tk.StringVar(value="")
        ttk.Label(pc, textvariable=self.class_desc_var, foreground="#555").grid(
            row=2, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(0, 3))
        self._update_class_desc()

        # File list with scrollbar
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.file_listbox = tk.Listbox(
            list_frame, 
            yscrollcommand=scrollbar.set,
            selectmode=tk.EXTENDED,
            font=('Consolas', 10)
        )
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.file_listbox.yview)
        
        self.file_listbox.bind('<<ListboxSelect>>', self._on_file_select)
        self.file_listbox.bind('<Double-1>', self._on_file_double_click)

        # NEW: Right-click context menu
        self.file_context_menu = tk.Menu(self.file_listbox, tearoff=0)
        self.file_context_menu.add_command(label="Toggle Direction (BF ⇄ FB)", command=self._toggle_line_direction)
        self.file_context_menu.add_command(label="Toggle Include/Exclude", command=self._toggle_line_used)
        self.file_context_menu.add_separator()
        self.file_context_menu.add_command(label="View Details", command=lambda: self.notebook.select(0))
        self.file_listbox.bind('<Button-3>', self._show_context_menu)

        # Summary label
        self.summary_label = ttk.Label(parent, text="No files loaded")
        self.summary_label.pack(pady=5)
    
    def _create_details_panel(self, parent: ttk.Frame):
        """Create the line details panel."""
        # Details frame
        details = ttk.LabelFrame(parent, text="Selected Line Details / פרטי הקו הנבחר")
        details.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create grid of labels
        labels = [
            ("Filename / שם קובץ:", "filename"),
            ("Start Point / נק' התחלה:", "start_point"),
            ("End Point / נק' סיום:", "end_point"),
            ("Method / שיטה:", "method"),
            ("Setups / מעמדים:", "setups"),
            ("Total Distance / מרחק:", "distance"),
            ("Height Diff / הפרש גובה:", "height_diff"),
            ("Status / סטטוס:", "status"),
        ]
        
        self.detail_vars = {}
        self._detail_line = None
        for i, (label_text, var_name) in enumerate(labels):
            ttk.Label(details, text=label_text).grid(row=i, column=0, sticky=tk.W, padx=5, pady=2)
            var = tk.StringVar(value="-")
            self.detail_vars[var_name] = var
            ttk.Label(details, textvariable=var).grid(row=i, column=1, sticky=tk.W, padx=5, pady=2)
        r = len(labels) - 1

        # Quick action button
        ttk.Button(details, text="Adjust This Line / תאם קו זה", 
                   command=self._adjust_selected_line).grid(row=r + 1, column=0, columnspan=2, pady=10)
        
        # Setups table
        setups_frame = ttk.LabelFrame(parent, text="Setups / מעמדים")
        setups_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        columns = ('From', 'To', 'Rb', 'Rf', 'dH', 'Dist')
        self.setups_tree = ttk.Treeview(setups_frame, columns=columns, show='headings', height=10)
        
        for col in columns:
            self.setups_tree.heading(col, text=col)
            self.setups_tree.column(col, width=80)
        
        setups_scroll = ttk.Scrollbar(setups_frame, orient=tk.VERTICAL, command=self.setups_tree.yview)
        self.setups_tree.configure(yscrollcommand=setups_scroll.set)
        
        self.setups_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        setups_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _create_validation_panel(self, parent: ttk.Frame):
        """Create the validation results panel with enhanced features (Phase 2, Items 1,2,7,8,11)."""
        # Action buttons frame at top
        action_frame = ttk.Frame(parent)
        action_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        ttk.Label(action_frame, text="Actions / פעולות:").pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Toggle Direction / הפוך כיוון",
                   command=self._toggle_validation_direction).pack(side=tk.LEFT, padx=2)
        ttk.Button(action_frame, text="Toggle Use / שנה שימוש",
                   command=self._toggle_validation_use).pack(side=tk.LEFT, padx=2)
        ttk.Button(action_frame, text="Refresh / רענן",
                   command=self._validate_all).pack(side=tk.LEFT, padx=2)

        # Create container frame for treeview and scrollbars (to allow grid inside pack)
        tree_container = ttk.Frame(parent)
        tree_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Enhanced columns with Δh (Measured) and better status
        columns = ('File', 'Start', 'End', 'Setups', 'Distance', 'dH', 'Δh_Meas', 'Status', 'Details')
        self.validation_tree = ttk.Treeview(tree_container, columns=columns, show='headings')

        # Dynamic headers will be set in _validate_all based on data
        self.validation_tree.heading('File', text='File')
        self.validation_tree.heading('Start', text='Start')
        self.validation_tree.heading('End', text='End')
        self.validation_tree.heading('Setups', text='Setups')
        self.validation_tree.heading('Distance', text='Distance')  # Will be updated dynamically
        self.validation_tree.heading('dH', text='dH [m]')
        self.validation_tree.heading('Δh_Meas', text='Δh (Meas) [mm]')
        self.validation_tree.heading('Status', text='Status')
        self.validation_tree.heading('Details', text='Details')

        self.validation_tree.column('File', width=100)
        self.validation_tree.column('Start', width=80)
        self.validation_tree.column('End', width=80)
        self.validation_tree.column('Setups', width=60)
        self.validation_tree.column('Distance', width=80)
        self.validation_tree.column('dH', width=100)
        self.validation_tree.column('Δh_Meas', width=100)
        self.validation_tree.column('Status', width=120)
        self.validation_tree.column('Details', width=400)  # Increased width for better readability

        # Add both vertical and horizontal scrollbars using grid inside container
        v_scrollbar = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.validation_tree.yview)
        h_scrollbar = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.validation_tree.xview)
        self.validation_tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)

        # Use grid inside the container
        self.validation_tree.grid(row=0, column=0, sticky='nsew')
        v_scrollbar.grid(row=0, column=1, sticky='ns')
        h_scrollbar.grid(row=1, column=0, sticky='ew')

        # Configure grid weights for proper resizing
        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)
    
    def _create_analysis_panel(self, parent: ttk.Frame):
        """Create the analysis results panel."""
        self.analysis_text = scrolledtext.ScrolledText(parent, font=('Consolas', 10))
        self.analysis_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_log_panel(self, parent: ttk.Frame):
        """Create the log panel."""
        bar = ttk.Frame(parent)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        ttk.Button(bar, text="Export Log... / ייצוא יומן", command=self._export_log).pack(side=tk.LEFT)
        ttk.Button(bar, text="Clear Log / נקה",
                   command=lambda: self.log_text.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=5)
        self.log_text = scrolledtext.ScrolledText(parent, font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text.tag_configure("WARNING", foreground="#E65100")
        self.log_text.tag_configure("ERROR", foreground="#C62828")
    
    def _create_status_bar(self):
        """Create the status bar with class selector."""
        status_frame = ttk.Frame(self.root, relief=tk.SUNKEN)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)

        # Status message (left side)
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor=tk.W)
        status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # project folder (right side)
        self.project_dir_var = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.project_dir_var, anchor=tk.E,
                  font=('Arial', 9)).pack(side=tk.RIGHT, padx=10)

    def _set_status(self, message: str):
        """Update status bar."""
        self.status_var.set(message)
        self.root.update_idletasks()

    def _sync_default_class(self):
        """Keep the saved default class (used by validators called without a
        class) equal to the project class shown in the panel."""
        try:
            from ..config.israel_survey_regulations import set_default_class
            set_default_class(self._project_class())
        except Exception:
            pass

    def _update_class_desc(self):
        try:
            from ..config.israel_survey_regulations import get_class_parameters_by_name
            p = get_class_parameters_by_name(self._project_class())
            self.class_desc_var.set(f"±{p.tolerance_coefficient:g} mm·√km | requires "
                                    f"{p.required_method} | sight ≤ "
                                    f"{p.max_sight_distance_geometric_m:g} m")
        except Exception:
            self.class_desc_var.set("")

    def _on_class_changed(self, event=None):
        """Project class changed: it applies to EVERY line (per-line classes
        are cleared), re-validate."""
        selected_class = self._project_class()
        self._sync_default_class()
        self._update_class_desc()
        own = [ln for ln in self.lines if getattr(ln, "leveling_class", None)]
        for ln in own:
            ln.leveling_class = None
        self._set_status(f"Class {selected_class} for all lines")
        self._log(f"Precision class -> {selected_class} (all lines)")
        if self.lines:
            self._validate_all()
            if self._detail_line is not None:
                self._show_line_details(self._detail_line)

    def _on_method_changed(self, event=None):
        """Project measure method changed: set on every line, re-validate."""
        method = self.method_var.get() or DEFAULT_METHOD
        self.project_method = method
        self._log(f"Measure method -> {method} (all lines)")
        if self.lines:
            self._apply_project_method(self.lines)
            self._validate_all()
            if self._detail_line is not None:
                self._show_line_details(self._detail_line)

    def _apply_project_method(self, lines):
        """Give *lines* the project method; warn when a raw file's readings
        show another method (the declared one is used)."""
        method = self.project_method or DEFAULT_METHOD
        differ = []
        for ln in lines:
            detected = getattr(ln, "measured_method", None)
            if detected is None and not getattr(ln, "is_summary", False) and ln.setups:
                detected = str(ln.method.value if hasattr(ln.method, "value") else ln.method)
                ln.measured_method = detected
            if detected and detected != method:
                differ.append(f"{ln.filename} ({detected})")
            ln.method = method
        if differ:
            self._log(f"Measure method {method} set for all lines; the readings of "
                      f"{len(differ)} file(s) look different: " + ", ".join(differ[:10])
                      + (" ..." if len(differ) > 10 else ""), level="WARNING")

    def _show_class_info(self):
        """Show information about the currently selected class."""
        from ..config.israel_survey_regulations import get_class_parameters_by_name

        selected_class = self.class_selector_var.get()
        params = get_class_parameters_by_name(selected_class)

        info_text = f"""Class {selected_class} Parameters (Survey of Israel Directive ג2)

Tolerance: ±{params.tolerance_coefficient} mm × √(Distance_km)

Distance Limits:
• Max Line Length: {params.max_line_length_km if params.max_line_length_km else 'Unlimited'} km

Sight Distance Limits:
• Geometric Leveling: {params.max_sight_distance_geometric_m} m
• Trigonometric Leveling: {params.max_sight_distance_trigonometric_m} m

Measurement Requirements:
• Method: {params.required_method} {'(Back-Fore-Fore-Back)' if params.required_method == 'BFFB' else '(Back-Fore)'}
• Double-Run Required: {'Yes' if params.requires_double_run else 'No'}

Distance Balance:
• Max Single Setup Imbalance: {params.max_single_distance_imbalance_m} m
• Max Cumulative Imbalance: {params.max_cumulative_distance_imbalance_m} m

Special Requirements:"""

        if params.requires_invar_staff:
            info_text += "\n• Invar Staff Required"
        if params.requires_staff_supports:
            info_text += "\n• Staff Supports Required"
        if params.requires_calibration_monthly:
            info_text += "\n• Monthly Calibration Required"
        if params.requires_orthometric_correction:
            info_text += "\n• Orthometric Correction Required"
        if params.max_instrument_error_mm_per_km:
            info_text += f"\n• Max Instrument Error: {params.max_instrument_error_mm_per_km} mm/km"
        if params.max_days_for_double_run:
            info_text += f"\n• Complete Double-Run Within: {params.max_days_for_double_run} days"

        if not any([params.requires_invar_staff, params.requires_staff_supports,
                   params.requires_calibration_monthly, params.requires_orthometric_correction,
                   params.max_instrument_error_mm_per_km, params.max_days_for_double_run]):
            info_text += "\n• None"

        messagebox.showinfo(f"Class {selected_class} Information", info_text)
    
    def _log(self, message: str, level: str = "INFO", source: str = "app"):
        """Add a timestamped entry to the Log tab and the project .log file."""
        from ..project_log import format_entry
        line = format_entry(str(level).upper(), source, message)
        tag = str(level).upper() if str(level).upper() in ("WARNING", "ERROR") else None
        self.log_text.insert(tk.END, line + "\n", (tag,) if tag else ())
        self.log_text.see(tk.END)
        self.project_log.write_line(line)

    def _install_log_capture(self):
        """Python logging of geodetic_tool.* (INFO and up) -> Log tab.  Records
        from worker threads are queued to the Tk thread with after()."""
        from ..project_log import install_handler
        import queue as _queue
        import threading as _th
        main = _th.main_thread()
        self._log_queue = _queue.Queue()

        def _cb(level, source, msg):
            if _th.current_thread() is main:
                self._log(msg, level, source)
            else:                       # Tk is not thread-safe: queue it
                self._log_queue.put((msg, level, source))
        self._log_handler = install_handler(_cb)
        self._drain_log_queue()

    def _drain_log_queue(self):
        """Move log entries queued by worker threads into the Log tab."""
        try:
            while True:
                msg, level, source = self._log_queue.get_nowait()
                self._log(msg, level, source)
        except Exception:
            pass
        try:
            self.root.after(200, self._drain_log_queue)
        except Exception:
            pass

    def _export_log(self):
        """Save the whole Log tab to a .log / .txt file (UTF-8)."""
        from ..project_log import write_log_file
        text = self.log_text.get("1.0", tk.END)
        if not text.strip():
            messagebox.showinfo("Export Log", "The log is empty.")
            return
        name = self._project_name() or "geodetic"
        path = filedialog.asksaveasfilename(
            title="Export Log", defaultextension=".log",
            initialdir=self.project_dir or None,
            initialfile=f"{name}_log_{datetime.now():%d_%m_%Y_%H_%M}.log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            written = write_log_file(path, text, self._project_name())
        except OSError as e:
            messagebox.showerror("Export Log", f"Could not write the log file:\n{e}")
            return
        self._log(f"Log exported to {written}")

    # ------------------------------------------------------------------ #
    # Project name and folder
    # ------------------------------------------------------------------ #
    def _project_name(self) -> str:
        n = self.current_project.name if self.current_project else ""
        return "" if n in ("", "Unnamed Project") else n

    def _start_project_folder(self, file_paths, titles) -> bool:
        """New project: name it <Description>_DD_MM_YYYY_H_MM and create
        <chosen parent>/<name>/.  Returns False when the user cancels."""
        from ..project_naming import describe_files, make_project_name, project_folder
        name = self._project_name() or make_project_name(describe_files(file_paths, titles))
        parent = filedialog.askdirectory(
            title="Where should the new project folder be created? / היכן ליצור את תיקיית הפרויקט",
            initialdir=self.last_parent_dir or str(Path.home()))
        if not parent:
            return False
        try:
            folder = project_folder(parent, name)
        except OSError as e:
            messagebox.showerror("Project folder", f"Could not create the project folder:\n{e}")
            return False
        self.current_project.name = name
        self._set_project_dir(folder)
        self._log(f"Project name: {name}")
        return True

    def _set_project_dir(self, folder: str):
        self.project_dir = folder or ""
        if folder:
            self.last_parent_dir = str(Path(folder).parent)
        name = self._project_name()
        self.root.title("Geodetic Leveling Tool" + (f" - {name}" if name else ""))
        try:
            self.project_dir_var.set(f"Project folder: {folder}" if folder else "")
        except Exception:
            pass
        if folder and name:
            self.project_log.open(str(Path(folder) / f"{name}.log"),
                                  self.log_text.get("1.0", tk.END), name)
            self._log(f"Project folder: {folder}")
        else:
            self.project_log.close()

    def _project_file_path(self) -> str:
        name = self._project_name()
        return str(Path(self.project_dir) / f"{name}.json") if self.project_dir and name else ""

    def _autosave_project(self):
        """Write <name>.json into the project folder (no dialog)."""
        path = self._project_file_path()
        if not path:
            return
        try:
            self.current_project.lines = self.lines
            self.current_project.point_overrides = dict(self.point_overrides)
            self.current_project.point_origin = dict(self.point_origin)
            self.current_project.project_path = path
            self.project_manager.save_project(self.current_project, format="json")
        except Exception as e:
            self._log(f"Project autosave failed: {e}", level="WARNING")

    def _export_project_zip(self):
        if not self.project_dir or not Path(self.project_dir).is_dir():
            messagebox.showinfo("Export Project as ZIP", "No project folder yet - load files first.")
            return
        self._autosave_project()
        folder = Path(self.project_dir)
        path = filedialog.asksaveasfilename(
            title="Export Project as ZIP", defaultextension=".zip",
            initialdir=str(folder.parent), initialfile=folder.name + ".zip",
            filetypes=[("ZIP archive", "*.zip")])
        if not path:
            return
        from ..project_naming import zip_folder
        try:
            n = zip_folder(str(folder), path)
        except OSError as e:
            messagebox.showerror("Export Project as ZIP", str(e))
            return
        self._log(f"Project exported as ZIP: {path} ({n} files)")
    
    def _open_files(self):
        """Open file dialog to select files."""
        filetypes = [
            ("All supported", "*.DAT *.dat *.RAW *.raw *.GSI *.gsi *.rez *.REZ *.FA0 *.fa0 *.FA1 *.fa1"),
            ("Trimble DAT", "*.DAT *.dat"),
            ("Leica RAW", "*.RAW *.raw"),
            ("Rez Summary", "*.rez *.REZ"),
            ("FA0 Input", "*.FA0 *.fa0"),
            ("FA1 Report", "*.FA1 *.fa1"),
            ("All files", "*.*")
        ]
        
        files = filedialog.askopenfilenames(
            title="Select leveling files",
            filetypes=filetypes
        )
        
        if files:
            self._load_files(list(files))
    
    def _open_folder(self):
        """Open folder and load all supported files."""
        folder = filedialog.askdirectory(title="Select folder with leveling files")

        if folder:
            exts = (".dat", ".raw", ".gsi", ".rez", ".fa0", ".fa1")
            files = sorted(p for p in Path(folder).rglob("*")
                           if p.is_file() and p.suffix.lower() in exts)
            
            if files:
                self._load_files([str(f) for f in files])
            else:
                messagebox.showinfo("No Files", "No supported files found in folder")
    
    # ------------------------------------------------------------------ #
    # Benchmark database
    # ------------------------------------------------------------------ #
    def _db(self):
        """The shared DB manager when configured, else None."""
        try:
            from ..db.benchmark_db import get_db_manager
            mgr = get_db_manager()
            return mgr if mgr.is_configured() else None
        except Exception:
            return None

    def _show_db_settings(self):
        from .db_settings_dialog import DBSettingsDialog
        dlg = DBSettingsDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.saved:
            self._log("Database connection saved")
            if self.lines and self._db() is not None and messagebox.askyesno(
                    "Database", "Look up the coordinates of the loaded points now?"):
                self._update_point_coordinates()

    def _verify_points(self, new_lines) -> bool:
        """Point Verification for freshly parsed lines (renamed / filtered in
        place).  Returns False when the user cancelled loading.  Without a
        reachable DB nothing happens and loading continues."""
        db = self._db()
        if db is None:
            return True
        from .db_settings_dialog import ensure_db_ready
        if not ensure_db_ready(self.root, log=self._log):
            return True
        from ..db.resolution import PointResolver
        from .point_resolution_dialog import ask_point_resolution
        resolver = PointResolver(db, self.point_overrides, self.point_origin, log=self._log)
        self._set_status("Matching points with the benchmark database...")
        self.root.update_idletasks()
        try:
            outcome = resolver.resolve(new_lines, ask_point_resolution(self.root))
        except Exception as exc:
            self._log(f"Point verification failed ({exc}) - loading without it")
            return True
        if outcome is None:
            return False
        self.point_overrides = resolver.point_overrides
        self.point_origin = resolver.point_origin
        for old, new in outcome.remap.items():
            if old in self.file_benchmarks and new not in self.file_benchmarks:
                # keep the header order of the fixed points
                self.file_benchmarks = {(new if k == old else k): v
                                        for k, v in self.file_benchmarks.items()}
            if old in self.file_approx and new not in self.file_approx:
                self.file_approx[new] = self.file_approx.pop(old)
            self._log(f"Point Verification: '{old}' -> '{new}'")
        if outcome.excluded:
            self._log("Point Verification: excluded " + ", ".join(sorted(outcome.excluded)))
        self._log(f"Point Verification: {outcome.auto_matched} point(s) matched "
                  f"automatically by DB / measured distances")
        return True

    def _update_point_coordinates(self):
        """EPSG:2039 coordinates for all loaded points (DB + topology
        estimates), stored in the project benchmarks for the exports."""
        db = self._db()
        if db is None:
            return
        from .db_settings_dialog import ensure_db_ready
        if not ensure_db_ready(self.root, log=self._log):
            return
        from ..db.resolution import resolve_coordinates
        try:
            self.point_coords = resolve_coordinates(self.lines, db)
        except Exception as exc:
            self._log(f"Coordinate lookup failed: {exc}")
            return
        bms = self.current_project.benchmarks
        for pid, c in self.point_coords.items():
            if c.estimated or c.height is None:
                continue
            rec = c.record
            desc = "DB: " + " ".join(str(v) for v in (
                getattr(rec, "name", None), getattr(rec, "shem_darga_gova", None),
                getattr(rec, "kfar_aher_name", None)) if v)
            bms[pid] = Benchmark(point_id=pid, height=c.height, easting=c.easting,
                                 northing=c.northing, description=desc)
        n_db = sum(1 for c in self.point_coords.values() if not c.estimated)
        n_est = len(self.point_coords) - n_db
        self._log(f"Coordinates: {n_db} point(s) from the DB, {n_est} estimated "
                  f"from the measured network")

    def _coordinate_manager(self):
        """CoordinateManager (WGS 84) for GeoJSON export, or None."""
        coords = dict(self.point_coords)
        if not coords:           # e.g. a reopened project: use stored benchmarks
            for pid, bm in (self.current_project.benchmarks or {}).items():
                if bm.easting is not None and bm.northing is not None:
                    from ..db.resolution import PointCoordinate
                    coords[pid] = PointCoordinate(bm.easting, bm.northing, bm.height, False)
        if not coords:
            return None
        from ..gis.geojson_export import CoordinateManager
        from ..gis.itm import itm_to_wgs84
        cm = CoordinateManager()
        for pid, c in coords.items():
            lon, lat = itm_to_wgs84(c.easting, c.northing)
            cm.add_point(pid, lon, lat, c.height or 0.0)
        return cm

    def _db_height(self, pid: str):
        db = self._db()
        if db is None:
            return None
        c = self.point_coords.get(pid)
        if c is not None and not c.estimated and c.height is not None:
            return c.height
        try:
            rec = db.resolve_benchmark(pid)
            return None if rec is None or rec.gova_ort is None else float(rec.gova_ort)
        except Exception:
            return None

    def _db_heights(self, pids) -> Dict[str, float]:
        """DB orthometric heights of *pids* in one batched lookup."""
        db = self._db()
        if db is None:
            return {}
        pids = [p for p in pids if p]
        out = {}
        try:
            todo = [p for p in pids if p not in self.point_coords]
            if todo:
                db.prefetch(todo)
            for p in pids:
                h = self._db_height(p)
                if h is not None:
                    out[p] = h
        except Exception as exc:
            self._log(f"DB height lookup failed: {exc}")
        return out

    def _load_files(self, file_paths: List[str]):
        """Load and parse files, verify the point IDs against the benchmark DB
        (when configured), then add the lines to the project."""
        self._set_status("Loading files...")
        loaded = set(self.file_paths)
        parsed_files = []                  # (path, lines, benchmarks)
        titles = {}                        # path -> header description (FA0/FA1/REZ)
        new_project = not self.lines
        for file_path in file_paths:
            if file_path in loaded:
                continue
            loaded.add(file_path)
            try:
                parser = create_parser(file_path)
                if parser is None:
                    raise ValueError("unsupported file type")
                # A summary file (.FA0 / .FA1 / .rez) holds one line per row
                parsed = parser.parse_many(file_path)
                for line in parsed:
                    line.source_path = str(Path(file_path).resolve())
                parsed_files.append((file_path, parsed,
                                     dict(getattr(parser, "benchmarks", None) or {})))
                self.file_approx.update(getattr(parser, "approx_heights", None) or {})
                titles[str(Path(file_path).resolve())] = getattr(parser, "title", "") or ""
            except Exception as e:
                self._log(f"Error loading {Path(file_path).name}: {str(e)}")

        new_lines = [ln for _, lines, _ in parsed_files for ln in lines]
        if not new_lines:
            self._set_status("Ready")
            return

        # Known heights from FA0/FA1 first, so renames below carry them along
        prev_benchmarks = dict(self.file_benchmarks)
        for _, _, bms in parsed_files:
            self.file_benchmarks.update(bms)
        if not self._verify_points(new_lines):
            self.file_benchmarks = prev_benchmarks
            self._log("Loading cancelled in Point Verification")
            self._set_status("Ready")
            return
        kept = {id(ln) for ln in new_lines}       # verification may drop lines

        # New project: auto name + its own folder for every project file
        if new_project and not self.project_dir:
            if not self._start_project_folder(
                    [str(Path(fp).resolve()) for fp, _, _ in parsed_files], titles):
                self._log("No project folder chosen - the project stays unsaved "
                          "until Save Project", level="WARNING")
        self._apply_project_method([ln for ln in new_lines if id(ln) in kept])

        for file_path, parsed, bms in parsed_files:
            lines = [ln for ln in parsed if id(ln) in kept]
            self.file_paths.append(file_path)
            display_name = Path(file_path).name
            for line in lines:
                self.lines.append(line)
                used_marker = "✓" if line.is_used else "✗"
                label = line.filename if len(parsed) > 1 else display_name
                self.file_listbox.insert(
                    tk.END, f"{used_marker} {label}: {line.start_point} → {line.end_point}")
            if len(parsed) > 1:
                self._log(f"Loaded: {display_name} ({len(lines)} lines, "
                          f"{len(bms)} known heights)")
            else:
                self._log(f"Loaded: {display_name}")

        if self._db() is not None:
            self._update_point_coordinates()

        # Update summary
        total_dist = sum(line.total_distance for line in self.lines)
        self.summary_label.config(text=f"{len(self.lines)} files, {total_dist:.0f} m total")
        self._autosave_project()
        self._set_status("Ready")

    def _clear_files(self):
        """Clear all loaded files (Item 16: logs to removed files report)."""
        from datetime import datetime

        # Log all cleared files to removed files report
        for line in self.lines:
            self.removed_files_log.append({
                'timestamp': datetime.now(),
                'filename': line.filename,
                'action': 'Cleared',
                'reason': 'User cleared all files',
                'start_point': line.start_point,
                'end_point': line.end_point,
                'distance_m': line.total_distance
            })

        self.lines.clear()
        self.file_paths.clear()
        self.file_benchmarks.clear()
        self.file_approx.clear()
        self.point_coords = {}             # point choices (overrides) are kept
        db = self._db()
        if db is not None:
            db.clear_session_cache()
        self.file_listbox.delete(0, tk.END)
        self._clear_details()
        self.summary_label.config(text="No files loaded")
        self._log("Cleared all files")
        # a new project starts with the defaults and its own folder
        self._set_project_dir("")
        from ..config.models import ProjectData
        self.current_project = ProjectData(name="Unnamed Project")
        self.class_selector_var.set(DEFAULT_CLASS)
        self.method_var.set(DEFAULT_METHOD)
        self.project_method = DEFAULT_METHOD
        self._sync_default_class()
        self._update_class_desc()
    
    def _reload_files(self):
        """Reload all files (same project: name, folder, class, method kept)."""
        paths = self.file_paths.copy()
        keep = (self.current_project, self.project_dir, self.class_selector_var.get(),
                self.method_var.get())
        self._clear_files()
        self.current_project, folder, cls, method = keep
        self.class_selector_var.set(cls)
        self.method_var.set(method)
        self.project_method = method
        self._sync_default_class()
        self._update_class_desc()
        self._set_project_dir(folder)
        self._load_files(paths)

    def _refresh_file_list(self):
        """Refresh file listbox to reflect current state of all lines (Phase 3, Item 14)."""
        # Clear listbox
        self.file_listbox.delete(0, tk.END)

        # Repopulate with all lines
        for line in self.lines:
            display_name = line.filename or f"{line.start_point}-{line.end_point}"
            used_marker = "✓" if line.is_used else "✗"
            self.file_listbox.insert(tk.END, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")

        # Update summary
        total_dist = sum(line.total_distance for line in self.lines if line.is_used)
        used_count = sum(1 for line in self.lines if line.is_used)
        self.summary_label.config(text=f"{used_count}/{len(self.lines)} files used, {total_dist:.0f} m total")

    def _on_file_select(self, event):
        """Handle file selection."""
        selection = self.file_listbox.curselection()
        if selection:
            index = selection[0]
            if index < len(self.lines):
                self._show_line_details(self.lines[index])
    
    def _on_file_double_click(self, event):
        """Handle double-click on file."""
        self._on_file_select(event)
        self.notebook.select(0)  # Switch to details tab

    def _show_context_menu(self, event):
        """Show context menu on right-click."""
        # Select the item under the cursor
        index = self.file_listbox.nearest(event.y)
        self.file_listbox.selection_clear(0, tk.END)
        self.file_listbox.selection_set(index)
        self.file_listbox.activate(index)

        # Show context menu
        try:
            self.file_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.file_context_menu.grab_release()
    
    def _project_class(self) -> str:
        try:
            return self.class_selector_var.get() or "H3"
        except Exception:
            return "H3"

    def _show_line_details(self, line: LevelingLine):
        """Display details for selected line."""
        self._detail_line = line
        self.detail_vars['filename'].set(line.filename or "-")
        self.detail_vars['start_point'].set(line.start_point or "-")
        self.detail_vars['end_point'].set(line.end_point or "-")
        self.detail_vars['method'].set(line.method.value if hasattr(line.method, 'value') else str(line.method) if line.method else "-")
        self.detail_vars['setups'].set(str(len(line.setups)))
        self.detail_vars['distance'].set(f"{line.total_distance:.2f} m")
        self.detail_vars['height_diff'].set(f"{line.total_height_diff:.5f} m")

        # Show actual validation result with the line's own class
        from ..validators import validate_lines
        validation_result = validate_lines([line], self._project_class())[0][1]
        if validation_result.is_valid:
            status_text = "valid - all checks passed"
            if validation_result.warnings:
                status_text += f" (⚠ {len(validation_result.warnings)} warning(s))"
        else:
            if validation_result.errors:
                status_text = f"invalid - {validation_result.errors[0]}"
            else:
                status_text = "invalid"

        self.detail_vars['status'].set(status_text)
        
        # Update setups table
        self.setups_tree.delete(*self.setups_tree.get_children())
        
        prev_point = line.start_point
        for i, setup in enumerate(line.setups):
            # For the last setup, use the line's end point, otherwise use TP naming
            if i == len(line.setups) - 1:
                next_point = line.end_point
            else:
                next_point = setup.to_point if hasattr(setup, 'to_point') and setup.to_point else f"TP{i+1}"
            
            rb = f"{setup.backsight_reading:.5f}" if setup.backsight_reading else "-"
            rf = f"{setup.foresight_reading:.5f}" if setup.foresight_reading else "-"
            dh = f"{setup.height_diff:.5f}" if setup.height_diff else "-"
            dist = f"{(setup.distance_back + setup.distance_fore) / 2:.2f}" if setup.distance_back and setup.distance_fore else "-"
            
            self.setups_tree.insert('', tk.END, values=(
                prev_point, next_point, rb, rf, dh, dist
            ))
            
            prev_point = next_point
    
    def _clear_details(self):
        """Clear the details panel."""
        for var in self.detail_vars.values():
            var.set("-")
        self.setups_tree.delete(*self.setups_tree.get_children())
    
    def _validate_all(self):
        """Validate all loaded files with enhanced reporting (Phase 2, Items 1,2,11)."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return

        self._set_status("Validating...")

        # Clear previous results
        self.validation_tree.delete(*self.validation_tree.get_children())

        # Item 1: Determine dynamic unit for distance column
        if self.lines:
            avg_distance = sum(line.total_distance for line in self.lines) / len(self.lines)
            use_km = avg_distance > 1000.0
            distance_unit = "km" if use_km else "m"
            self.validation_tree.heading('Distance', text=f'Distance [{distance_unit}]')

        # Item 11: Detect double-run pairs for Δh (Measured) column
        double_run_pairs = detect_double_runs(self.lines)
        double_run_map = {}  # Maps line to its pair and misclosure
        analyzer = LoopAnalyzer()

        for fwd, ret in double_run_pairs:
            result = analyzer.analyze_double_run(fwd, ret)
            if result['valid']:
                # Store misclosure in mm for both forward and return
                delta_h_mm = result['misclosure_mm']
                double_run_map[id(fwd)] = {'pair': ret, 'delta_h': delta_h_mm}
                double_run_map[id(ret)] = {'pair': fwd, 'delta_h': delta_h_mm}

        # Validate all lines (each with its own class, else the project class)
        from ..validators import validate_lines
        results = validate_lines(self.lines, self._project_class())

        for line, result in results:
            # Item 2: Enhanced status with specific failure reasons
            if result.is_valid:
                status_text = "✓ PASS"
                status_detail = "All checks passed"
            else:
                # Determine primary failure reason
                if not result.endpoint_valid:
                    status_text = "✗ FAIL: Endpoint"
                    status_detail = "Invalid endpoint (turning point or numeric)"
                elif not result.naming_valid:
                    status_text = "✗ FAIL: Naming"
                    status_detail = "Front-to-back naming error"
                elif not result.tolerance_valid:
                    status_text = "✗ FAIL: Tolerance"
                    if line.misclosure:
                        status_detail = f"Misclosure {line.misclosure:.2f}mm exceeds tolerance"
                    else:
                        status_detail = "Exceeds tolerance limits"
                elif not result.data_complete:
                    status_text = "✗ FAIL: Incomplete"
                    status_detail = "Missing data or insufficient setups"
                else:
                    status_text = "✗ FAIL: Other"
                    status_detail = "; ".join(result.errors[:2]) if result.errors else "Validation failed"

            # Format distance with dynamic unit
            if use_km:
                distance_str = f"{line.total_distance / 1000.0:.3f}"
            else:
                distance_str = f"{line.total_distance:.2f}"

            # Item 11: Get Δh (Measured) for double-runs
            delta_h_str = "-"
            if id(line) in double_run_map:
                delta_h_str = f"{double_run_map[id(line)]['delta_h']:.2f}"

            # Add warnings to detail if present
            if result.warnings:
                if status_detail == "All checks passed":
                    status_detail = f"⚠ {'; '.join(result.warnings[:2])}"
                else:
                    status_detail += f" | ⚠ {result.warnings[0]}"

            self.validation_tree.insert('', tk.END, values=(
                line.filename or "-",
                line.start_point or "-",
                line.end_point or "-",
                len(line.setups),
                distance_str,
                f"{line.total_height_diff:.5f}",
                delta_h_str,
                status_text,
                status_detail
            ))

        # Switch to validation tab
        self.notebook.select(1)
        self._set_status("Validation complete")
        self._log(f"Validated {len(self.lines)} files ({len(double_run_pairs)} double-run pairs detected)")
    
    def _detect_double_runs(self):
        """Double-run check of Directive G2 (2021) App. C: sections measured
        more than once, delta = dH_fwd + dH_ret vs k*sqrt(L) (L one direction,
        the line's class else the project class), mean dH = (dH_fwd -
        dH_ret)/2 - the same numbers as Step 1 of the adjustment."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        from ..engine.network_prep import analyze_double_runs
        results = analyze_double_runs(self.lines, self._project_class())

        t = self.analysis_text
        t.delete('1.0', tk.END)
        t.insert(tk.END, "=== DOUBLE-RUN ANALYSIS / ניתוח הלוך-שוב ===\n")
        t.insert(tk.END, "Directive G2 (2021) App. C: δ = ΔH fwd + ΔH ret, |δ| ≤ k·√L "
                         "(L one direction)\n\n")
        if not results:
            t.insert(tk.END, "No section measured more than once.\n")
        for r in results:
            t.insert(tk.END, f"Section: {r.from_point} ↔ {r.to_point}"
                             + (f"  ({r.n_runs} runs)" if r.n_runs > 2 else "") + "\n")
            t.insert(tk.END, f"  Forward file:  {r.runs[0].filename}\n")
            t.insert(tk.END, "  Other run(s):  " + ", ".join(str(l.filename) for l in r.runs[1:]) + "\n")
            t.insert(tk.END, f"  Forward dH:    {r.forward_dh:.5f} m\n")
            if r.return_dh is not None:
                t.insert(tk.END, f"  {'Repeat' if r.same_direction else 'Return'} dH:     "
                                 f"{r.return_dh:.5f} m\n")
            what = ("spread" if r.n_runs > 2 else "repeat diff" if r.same_direction else "δ")
            t.insert(tk.END, f"  {what:<14}{r.discrepancy_mm:+.1f} mm\n")
            t.insert(tk.END, f"  Mean dH:       {r.mean_dh:.5f} m\n")
            t.insert(tk.END, f"  L (one dir.):  {r.distance / 1000.0:.3f} km\n")
            t.insert(tk.END, f"  Allowed:       ±{r.allowed_mm:.1f} mm ({r.leveling_class})\n")
            best = f"H{r.achieved}" if r.achieved else "none (exceeds H6)"
            t.insert(tk.END, "  Status:        " + ("✓ PASS" if r.ok else "✗ FAIL")
                     + f"   (class met: {best})\n\n")
        self.notebook.select(2)
        n_fail = sum(1 for r in results if not r.ok)
        self._log(f"Double-runs: {len(results)} section(s), {len(results) - n_fail} pass, "
                  f"{n_fail} fail", level="WARNING" if n_fail else "INFO")

    def _find_loops(self):
        """Find closed loops in the network."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        
        analyzer = LoopAnalyzer(self.lines)
        summary = analyzer.get_network_summary()
        
        self.analysis_text.delete('1.0', tk.END)
        self.analysis_text.insert(tk.END, "=== NETWORK ANALYSIS / ניתוח רשת ===\n\n")
        self.analysis_text.insert(tk.END, f"Points: {summary['num_points']}\n")
        self.analysis_text.insert(tk.END, f"Lines: {summary['num_lines']}\n")
        self.analysis_text.insert(tk.END, f"Loops found: {summary['num_loops']}\n\n")
        
        if summary['loops']:
            self.analysis_text.insert(tk.END, "=== LOOPS ===\n\n")
            for i, loop in enumerate(summary['loops'], 1):
                self.analysis_text.insert(tk.END, f"Loop {i}:\n")
                self.analysis_text.insert(tk.END, f"  Points: {' → '.join(loop.points)}\n")
                self.analysis_text.insert(tk.END, f"  Lines: {loop.num_lines}\n")
                self.analysis_text.insert(tk.END, f"  Distance: {loop.total_distance:.2f} m\n")
                self.analysis_text.insert(tk.END, f"  Misclosure: {loop.misclosure*1000:.3f} mm\n")
                self.analysis_text.insert(tk.END, f"  Class: {loop.tolerance_class or 'Exceeds all'}\n\n")
        
        self.notebook.select(2)
    
    def _open_adjustment(self, selected_lines=None):
        """The single adjustment window (network, or one line when
        *selected_lines* is given)."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return None
        from .adjustment_window import AdjustmentWindow
        cls = self._project_class()
        name = self._project_name() or "geo_level"
        win = AdjustmentWindow(self.root, self.lines, leveling_class=cls,
                               known_heights=self.file_benchmarks,
                               selected_lines=selected_lines, project_name=name,
                               output_dir=self.project_dir,
                               db_heights=self._db_heights if self._db() else None,
                               approx_heights=self.file_approx)
        self.root.wait_window(win)
        if win.result is not None:
            self._log(f"Adjustment completed ({win.result.method}, "
                      f"sigma0 = {win.result.mse_unit_weight * 1000:.2f} mm/sqrt(km))")
        return win

    def _adjust_selected_line(self):
        """'Adjust This Line': the adjustment window with only this line ticked."""
        selection = self.file_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select a line first")
            return
        index = selection[0]
        if index < len(self.lines):
            self._open_adjustment(selected_lines=[self.lines[index]])

    def _line_adjustment(self):
        """Analysis menu: adjust the selected line (same window)."""
        self._adjust_selected_line()

    def _network_adjustment(self):
        """Analysis menu: Network Adjustment (LSA)."""
        self._open_adjustment()

    def _merge_lines(self):
        """Open merge line segments dialog (Phase 3, Items 5, 12/13, 14)."""
        if not self.lines:
            messagebox.showinfo(
                "No Data",
                "Please load leveling files first."
            )
            return

        if len(self.lines) < 2:
            messagebox.showinfo(
                "Not Enough Lines",
                "Merge requires at least 2 lines."
            )
            return

        # Get selected lines from listbox (if any)
        selection = self.file_listbox.curselection()
        selected_indices = list(selection) if selection else None

        # Open merge dialog
        dialog = MergeDialog(self.root, self.lines, selected_indices)
        self.root.wait_window(dialog)

        if dialog.merged_line:
            # Refresh file listbox to show merged line and excluded originals
            self._refresh_file_list()
            self._log(f"Merge completed: {dialog.merged_line.filename}")

    def _export_results(self):
        """Export results to files."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        
        folder = self.project_dir if self.project_dir and Path(self.project_dir).is_dir() else \
            filedialog.askdirectory(title="Select Output Folder / בחר תיקיית יעד")
        if folder:
            try:
                from ..exporters import FA0Exporter, FTEGExporter
                from ..gis.geojson_export import GeoJSONExporter
                
                # Export FTEG (only used lines)
                stem = self._project_name() or "lines"
                fteg_path = Path(folder) / f"{stem}.FTEG"
                fteg = FTEGExporter()
                # Convert LevelingLine objects to MeasurementSummary format
                from ..config.models import MeasurementSummary
                observations = []
                for line in self.lines:
                    if not line.is_used:  # Skip unused lines
                        continue
                    obs = MeasurementSummary(
                        from_point=line.start_point,
                        to_point=line.end_point,
                        height_diff=line.total_height_diff,
                        distance=line.total_distance,
                        num_setups=len(line.setups),
                        bf_diff=0.0,  # Calculate if needed
                        year_month=line.date[:4] if line.date else "",
                        source_file=line.filename or "",
                        is_used=True
                    )
                    observations.append(obs)
                fteg.export(str(fteg_path), observations)

                # Export GeoJSON (only used lines)
                geojson_path = Path(folder) / f"{stem}.geojson"
                gj = GeoJSONExporter(self._coordinate_manager())
                used_lines = [line for line in self.lines if line.is_used]
                gj.export_lines(used_lines, str(geojson_path))

                used_count = len([line for line in self.lines if line.is_used])
                total_count = len(self.lines)
                messagebox.showinfo("Export",
                    f"Files exported to:\n{folder}\n\n"
                    f"Exported {used_count} of {total_count} lines (only 'Used' lines)")
                self._log(f"Exported to {folder}: {used_count}/{total_count} lines")
            except Exception as e:
                messagebox.showerror("Error", f"Export failed: {str(e)}")
    
    def _show_docs(self):
        """Show documentation."""
        docs_text = """
GEODETIC LEVELING TOOL - QUICK REFERENCE
=========================================

FILE FORMATS SUPPORTED:
• Trimble DAT, Leica RAW / GSI-8 / GSI-16 (one line per file)
• FA0 / FA1 / REZ summary files (one line per row; known
  heights in the file become fixed-point candidates)

WORKFLOW:
1. Load Files: File > Open Files or Open Folder
2. Validate: Analysis > Validate All
3. Review: line list, Line Details, Validation messages
4. Adjust: Analysis > Network Adjustment (LSA)
   (Line Adjustment opens the same window for one line)
5. Export: FA0 / FA1 / PDF from the adjustment window,
   REZ / GeoJSON from File > Export

ADJUSTMENT WINDOW (תיאום רשת):
- Prepare (Steps 1-2): forward/return runs averaged and
  checked against k*sqrt(L) (L one direction); approximate
  heights from the fixed points
- Choose fixed points, method (Parametric V=AX-L or
  Conditional Bv+w=0), weights (distance / setups / equal)
- Run: adjusted heights with sigma and 95%, residuals,
  BM-to-BM misclosures
- Single line: misclosure distributed by distance between
  two known heights

TOLERANCES  k * sqrt(L km), Directive G2 (2021):
• H1 3 mm   • H2 5 mm   • H3 10 mm
• H4 20 mm  • H5 30 mm  • H6 60 mm
        """
        messagebox.showinfo("Documentation", docs_text)

    def _show_class_settings(self):
        """Show class settings dialog (Phase 4, Item 4)."""
        dialog = ClassSettingsDialog(self.root)
        self.root.wait_window(dialog)
        self._log("Viewed class parameters")

    def _show_encoding_settings(self):
        """Show encoding settings dialog."""
        current_encoding = self.settings.encoding.output_encoding

        dialog = tk.Toplevel(self.root)
        dialog.title("Encoding Settings / הגדרות קידוד")
        fit_to_screen(dialog, 500, 300)
        dialog.transient(self.root)
        dialog.grab_set()

        # Main frame
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        title_label = ttk.Label(main_frame, text="File Encoding Configuration",
                               font=("Arial", 14, "bold"))
        title_label.pack(pady=(0, 20))

        # Current settings frame
        settings_frame = ttk.LabelFrame(main_frame, text="Current Settings", padding="15")
        settings_frame.pack(fill=tk.X, pady=10)

        # Output encoding
        output_frame = ttk.Frame(settings_frame)
        output_frame.pack(fill=tk.X, pady=5)
        ttk.Label(output_frame, text="Output Encoding (for exports):", width=30).pack(side=tk.LEFT)
        ttk.Label(output_frame, text=current_encoding,
                 font=("Courier", 10, "bold")).pack(side=tk.LEFT)

        # Input encoding
        input_frame = ttk.Frame(settings_frame)
        input_frame.pack(fill=tk.X, pady=5)
        ttk.Label(input_frame, text="Input Encoding (for reading files):", width=30).pack(side=tk.LEFT)
        ttk.Label(input_frame, text=self.settings.encoding.default_encoding,
                 font=("Courier", 10, "bold")).pack(side=tk.LEFT)

        # Info label
        info_text = (
            "Hebrew Support:\n"
            "• cp1255 (Windows-1255) - Recommended for Hebrew files\n"
            "• utf-8 - Universal encoding (may have issues with Hebrew)\n\n"
            "Current configuration uses cp1255 for proper Hebrew character support."
        )
        info_label = ttk.Label(main_frame, text=info_text, justify=tk.LEFT,
                              foreground="navy")
        info_label.pack(pady=20)

        # Close button
        close_btn = ttk.Button(main_frame, text="Close / סגור",
                              command=dialog.destroy)
        close_btn.pack(pady=10)

        self._log("Viewed encoding settings")

    def _manage_point_exclusion(self):
        """Open point exclusion dialog (Phase 4, Item 15)."""
        if not self.lines:
            messagebox.showinfo(
                "No Data",
                "Please load leveling files first."
            )
            return

        dialog = PointExclusionDialog(self.root, self.lines)
        self.root.wait_window(dialog)

        if dialog.excluded_lines:
            # Refresh file listbox to reflect exclusions
            self._refresh_file_list()
            self._log(f"Point exclusion: {len(dialog.excluded_lines)} line(s) affected")

    def _show_about(self):
        """Show about dialog."""
        about_text = """
Geodetic Leveling Tool
כלי פילוס גיאודטי

Version 1.0

Supports:
• Trimble DAT format
• Leica RAW/GSI format

Features:
• Data parsing and validation
• Double-run analysis
• Loop detection
• Line adjustment (תיאום קו)
• Network adjustment LSA (תיאום רשת)
• Export to FA0, FA1, FTEG, GeoJSON

© 2024
        """
        messagebox.showinfo("About / אודות", about_text)

    # NEW: Project Management Methods

    def _save_project(self):
        """Save current project to file."""
        if not self.lines:
            messagebox.showinfo("No Data", "No data to save")
            return

        # Sync lines and point-verification choices to project
        self.current_project.lines = self.lines
        self.current_project.point_overrides = dict(self.point_overrides)
        self.current_project.point_origin = dict(self.point_origin)

        # Unnamed (no file loaded through a new project yet): auto name
        if self.current_project.name == "Unnamed Project":
            from ..project_naming import make_project_name
            self.current_project.name = make_project_name("GeoLevel")

        # Ask for save location - the project folder by default
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Project", "*.json"), ("Pickle Project", "*.pickle"), ("All files", "*.*")],
            initialdir=self.project_dir or None,
            initialfile=f"{self.current_project.name}.json"
        )

        if filepath:
            try:
                format = "json" if filepath.endswith(".json") else "pickle"
                self.current_project.project_path = filepath
                saved_path = self.project_manager.save_project(self.current_project, format=format)
                if not self.project_dir:
                    self._set_project_dir(str(Path(saved_path).parent))
                messagebox.showinfo("Success", f"Project saved to:\n{saved_path}")
                self._log(f"Project saved: {saved_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save project: {str(e)}")

    def _load_project(self):
        """Load project from file."""
        filepath = filedialog.askopenfilename(
            title="Open Project",
            filetypes=[("Project files", "*.json *.pickle"), ("JSON Project", "*.json"), ("Pickle Project", "*.pickle"), ("All files", "*.*")]
        )

        if filepath:
            try:
                project = self.project_manager.load_project(filepath)
                self.current_project = project
                self.lines = project.lines
                self.file_paths = [line.filename for line in self.lines]
                # DB coordinates come from the saved benchmarks; no DB query
                self.point_overrides = dict(getattr(project, "point_overrides", {}) or {})
                self.point_origin = dict(getattr(project, "point_origin", {}) or {})
                self.point_coords = {}

                # Update UI
                self.file_listbox.delete(0, tk.END)
                for line in self.lines:
                    display_name = line.filename or f"{line.start_point}-{line.end_point}"
                    used_marker = "✓" if line.is_used else "✗"
                    self.file_listbox.insert(tk.END, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")

                total_dist = sum(line.total_distance for line in self.lines)
                self.summary_label.config(text=f"{len(self.lines)} files, {total_dist:.0f} m total")
                # one class / method; the folder of the project file
                methods = [str(ln.method.value if hasattr(ln.method, "value") else ln.method)
                           for ln in self.lines]
                from collections import Counter
                common = [m for m, _ in Counter(methods).most_common() if m in ("BF", "BFFB", "BBFF")]
                self.project_method = common[0] if common else DEFAULT_METHOD
                self.method_var.set(self.project_method)
                if len(set(methods)) > 1 or any(getattr(ln, "leveling_class", None) for ln in self.lines):
                    self._log("This project was saved with per-line settings; choosing Class / "
                              "Method applies one value to every line.", level="WARNING")
                self._set_project_dir(str(Path(filepath).parent))

                messagebox.showinfo("Success", f"Project loaded: {project.name}")
                self._log(f"Project loaded: {filepath}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load project: {str(e)}")

    def _create_joint_project(self):
        """Create a joint project from multiple source projects."""
        # Select multiple project files
        filepaths = filedialog.askopenfilenames(
            title="Select Source Projects",
            filetypes=[("Project files", "*.json *.pickle"), ("All files", "*.*")]
        )

        if not filepaths:
            return

        # Ask for joint project name
        name = simpledialog.askstring("Joint Project Name", "Enter name for the joint project:")
        if not name:
            return

        try:
            joint_project = self.project_manager.create_joint_project(name, list(filepaths))
            self.current_project = joint_project
            self.lines = joint_project.lines
            self.file_paths = [line.filename for line in self.lines]

            # Update UI
            self.file_listbox.delete(0, tk.END)
            for line in self.lines:
                display_name = line.filename or f"{line.start_point}-{line.end_point}"
                used_marker = "✓" if line.is_used else "✗"
                self.file_listbox.insert(tk.END, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")

            total_dist = sum(line.total_distance for line in self.lines)
            self.summary_label.config(text=f"{len(self.lines)} files, {total_dist:.0f} m total (JOINT PROJECT)")
            self.root.title(f"Geodetic Leveling Tool - {name} (Joint)")

            messagebox.showinfo("Success",
                f"Joint project created!\n\n"
                f"Lines: {len(self.lines)}\n"
                f"Source projects: {len(joint_project.source_projects)}\n\n"
                f"You can now edit without affecting source projects."
            )
            self._log(f"Joint project created: {name}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to create joint project: {str(e)}")

    def _show_project_properties(self):
        """Show project properties dialog."""
        if not self.current_project:
            return

        props_text = f"""Project: {self.current_project.name}
Type: {"Joint Project" if self.current_project.is_joint_project else "Single Project"}
Lines: {len(self.current_project.lines)}
Used Lines: {len(self.current_project.get_used_lines())}
Benchmarks: {len(self.current_project.benchmarks)}
Total Points: {len(self.current_project.get_all_points())}
"""

        if self.current_project.is_joint_project:
            props_text += f"\nSource Projects:\n"
            for src in self.current_project.source_projects:
                props_text += f"  - {src}\n"

        if self.current_project.project_path:
            props_text += f"\nSaved at: {self.current_project.project_path}"

        messagebox.showinfo("Project Properties", props_text)

    def _export_qgis(self):
        """Export project for QGIS."""
        if not self.lines:
            messagebox.showinfo("No Data", "Please load files first")
            return

        folder = self.project_dir if self.project_dir and Path(self.project_dir).is_dir() else \
            filedialog.askdirectory(title="Select Output Folder for QGIS Export")
        if folder:
            try:
                from ..gis.qgis_integration import QGISVirtualLayerBuilder

                # Sync lines to project
                self.current_project.lines = self.lines

                builder = QGISVirtualLayerBuilder()
                output_files = builder.export_for_qgis(
                    self.current_project, folder, include_geojson=True,
                    coord_manager=self._coordinate_manager())

                messagebox.showinfo("Export Complete",
                    f"QGIS files exported to:\n{folder}\n\n"
                    f"PyQGIS script: {Path(output_files['pyqgis_script']).name}\n"
                    f"README: {Path(output_files['readme']).name}\n\n"
                    f"See README_QGIS.txt for instructions."
                )
                self._log(f"QGIS export: {folder}")

            except Exception as e:
                messagebox.showerror("Error", f"QGIS export failed: {str(e)}")
                import traceback
                traceback.print_exc()

    def _toggle_line_direction(self):
        """Toggle direction of selected line (BF <-> FB)."""
        selection = self.file_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select a line first")
            return

        index = selection[0]
        if index < len(self.lines):
            line = self.lines[index]
            old_method = line.method
            old_start = line.start_point
            old_end = line.end_point

            # Toggle direction (swaps start/end and inverts dH)
            line.toggle_direction()

            # CRITICAL FIX: Update listbox entry to reflect new start/end points
            # This prevents name duplication bugs (e.g., PointA -> PointA_1)
            display_name = line.filename or f"{line.start_point}-{line.end_point}"
            used_marker = "✓" if line.is_used else "✗"
            self.file_listbox.delete(index)
            self.file_listbox.insert(index, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")
            self.file_listbox.selection_set(index)  # Re-select the item

            # Update detail panel
            self._show_line_details(line)

            messagebox.showinfo("Direction Toggled",
                f"Line direction changed:\n"
                f"{old_method} → {line.method}\n\n"
                f"Old: {old_start} → {old_end}\n"
                f"New: {line.start_point} → {line.end_point}\n\n"
                f"Height difference inverted: {line.total_height_diff:.5f} m"
            )
            self._log(f"Toggled direction for {line.filename}: {old_start}→{old_end} to {line.start_point}→{line.end_point}")

    def _toggle_line_used(self):
        """Toggle is_used flag for selected line (Item 16: tracks exclusions)."""
        from datetime import datetime

        selection = self.file_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select a line first")
            return

        index = selection[0]
        if index < len(self.lines):
            line = self.lines[index]
            old_status = line.is_used
            line.is_used = not line.is_used

            # Track file exclusion in removed files log (Item 16)
            if old_status and not line.is_used:
                # File was excluded
                self.removed_files_log.append({
                    'timestamp': datetime.now(),
                    'filename': line.filename,
                    'action': 'Excluded',
                    'reason': 'User toggled file to excluded state',
                    'start_point': line.start_point,
                    'end_point': line.end_point,
                    'distance_m': line.total_distance
                })

            # Update display
            display_name = line.filename or f"{line.start_point}-{line.end_point}"
            used_marker = "✓" if line.is_used else "✗"
            self.file_listbox.delete(index)
            self.file_listbox.insert(index, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")
            self.file_listbox.selection_set(index)

            status = "Included" if line.is_used else "Excluded"
            self._log(f"{status}: {line.filename}")

    def _toggle_validation_direction(self):
        """Toggle direction for line selected in validation table (Item 7)."""
        selection = self.validation_tree.selection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select a line from the validation table first")
            return

        # Get selected item index
        selected_item = selection[0]
        item_values = self.validation_tree.item(selected_item)['values']
        filename = item_values[0]

        # Find corresponding line
        line = None
        for l in self.lines:
            if (l.filename or "-") == filename:
                line = l
                break

        if not line:
            messagebox.showerror("Error", "Could not find corresponding line")
            return

        old_start = line.start_point
        old_end = line.end_point

        # Toggle direction
        line.toggle_direction()

        # Update file listbox if the line is displayed there
        for i, l in enumerate(self.lines):
            if l is line:
                display_name = line.filename or f"{line.start_point}-{line.end_point}"
                used_marker = "✓" if line.is_used else "✗"
                self.file_listbox.delete(i)
                self.file_listbox.insert(i, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")
                break

        # Refresh validation table (Item 8: immediate recalculation)
        self._validate_all()

        self._log(f"Toggled direction in validation: {old_start}→{old_end} to {line.start_point}→{line.end_point}")

    def _toggle_validation_use(self):
        """Toggle is_used flag for line selected in validation table (Item 8: immediate recalculation)."""
        from datetime import datetime

        selection = self.validation_tree.selection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select a line from the validation table first")
            return

        # Get selected item index
        selected_item = selection[0]
        item_values = self.validation_tree.item(selected_item)['values']
        filename = item_values[0]

        # Find corresponding line
        line = None
        line_index = -1
        for i, l in enumerate(self.lines):
            if (l.filename or "-") == filename:
                line = l
                line_index = i
                break

        if not line:
            messagebox.showerror("Error", "Could not find corresponding line")
            return

        old_status = line.is_used
        line.is_used = not line.is_used

        # Track file exclusion in removed files log (Item 16)
        if old_status and not line.is_used:
            self.removed_files_log.append({
                'timestamp': datetime.now(),
                'filename': line.filename,
                'action': 'Excluded',
                'reason': 'User toggled file to excluded state from validation table',
                'start_point': line.start_point,
                'end_point': line.end_point,
                'distance_m': line.total_distance
            })

        # Update file listbox
        if line_index >= 0:
            display_name = line.filename or f"{line.start_point}-{line.end_point}"
            used_marker = "✓" if line.is_used else "✗"
            self.file_listbox.delete(line_index)
            self.file_listbox.insert(line_index, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")

        # Refresh validation table (Item 8: immediate recalculation)
        self._validate_all()

        status = "Included" if line.is_used else "Excluded"
        self._log(f"{status} from validation table: {line.filename}")

    def _view_removed_files_report(self):
        """View removed/excluded files report (Item 16)."""
        if not self.removed_files_log:
            messagebox.showinfo("No Removed Files",
                "No files have been removed or excluded during this session.\n\n"
                f"Session started: {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return

        # Create report dialog
        report_dialog = tk.Toplevel(self.root)
        report_dialog.title("Removed Files Report - דוח קבצים שהוסרו")
        fit_to_screen(report_dialog, 900, 600)
        report_dialog.transient(self.root)

        # Header
        header_frame = ttk.Frame(report_dialog)
        header_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(header_frame, text="Removed/Excluded Files Report",
                 font=('Arial', 14, 'bold')).pack(anchor=tk.W)
        ttk.Label(header_frame,
                 text=f"Session start: {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')}",
                 font=('Arial', 10)).pack(anchor=tk.W)
        ttk.Label(header_frame,
                 text=f"Total removed/excluded: {len(self.removed_files_log)} file(s)",
                 font=('Arial', 10, 'bold'), foreground='red').pack(anchor=tk.W)

        # Report text area
        text_frame = ttk.Frame(report_dialog)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        report_text = scrolledtext.ScrolledText(text_frame, font=('Consolas', 10), wrap=tk.WORD)
        report_text.pack(fill=tk.BOTH, expand=True)

        # Build report content
        report_text.insert(tk.END, "=" * 100 + "\n")
        report_text.insert(tk.END, "REMOVED/EXCLUDED FILES REPORT\n")
        report_text.insert(tk.END, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        report_text.insert(tk.END, "=" * 100 + "\n\n")

        for i, entry in enumerate(self.removed_files_log, 1):
            report_text.insert(tk.END, f"[{i}] {entry['action'].upper()}\n")
            report_text.insert(tk.END, f"    Time:     {entry['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n")
            report_text.insert(tk.END, f"    File:     {entry['filename']}\n")
            report_text.insert(tk.END, f"    Points:   {entry['start_point']} → {entry['end_point']}\n")
            report_text.insert(tk.END, f"    Distance: {entry['distance_m']:.2f} m\n")
            report_text.insert(tk.END, f"    Reason:   {entry['reason']}\n")
            report_text.insert(tk.END, "-" * 100 + "\n\n")

        report_text.insert(tk.END, "=" * 100 + "\n")
        report_text.insert(tk.END, f"END OF REPORT - Total: {len(self.removed_files_log)} file(s)\n")
        report_text.configure(state='disabled')

        # Buttons
        btn_frame = ttk.Frame(report_dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(btn_frame, text="Export Report",
                  command=lambda: self._export_removed_files_report(report_text.get('1.0', tk.END))).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Clear Log",
                  command=lambda: self._clear_removed_files_log(report_dialog)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Close", command=report_dialog.destroy).pack(side=tk.RIGHT, padx=5)

    def _export_removed_files_report(self, report_content: str):
        """Export removed files report to text file."""
        from tkinter import filedialog

        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"removed_files_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )

        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(report_content)
                messagebox.showinfo("Export Successful", f"Report exported to:\n{filename}")
            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export report:\n{str(e)}")

    def _clear_removed_files_log(self, dialog=None):
        """Clear the removed files log."""
        if messagebox.askyesno("Confirm Clear",
            "Are you sure you want to clear the removed files log?\n\n"
            "This action cannot be undone."):
            self.removed_files_log.clear()
            self._log("Removed files log cleared")
            if dialog:
                dialog.destroy()
            messagebox.showinfo("Log Cleared", "Removed files log has been cleared.")


def main():
    """Main entry point for the GUI application."""
    root = tk.Tk()
    app = GeodeticToolGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
