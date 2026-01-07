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
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..parsers.base_parser import create_parser, detect_file_format
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

# Matplotlib imports - gracefully handle if not available
try:
    import matplotlib
    matplotlib.use('TkAgg')  # Set backend before importing pyplot
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Warning: matplotlib not available. Visualization features will be disabled.")


class BenchmarkDialog(tk.Toplevel):
    """Dialog for entering benchmark heights."""
    
    def __init__(self, parent, points: List[str], existing_heights: Dict[str, float] = None):
        super().__init__(parent)
        self.title("Enter Benchmark Heights / הזנת גבהים ידועים")
        self.geometry("400x500")
        self.transient(parent)
        self.grab_set()
        
        self.result = None
        self.points = points
        self.existing = existing_heights or {}
        self.entries = {}
        
        self._create_widgets()
        self.center_on_parent(parent)
    
    def center_on_parent(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
    
    def _create_widgets(self):
        # Instructions
        ttk.Label(self, text="Enter known heights for fixed points:").pack(pady=10)
        
        # Scrollable frame for points
        canvas = tk.Canvas(self)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Create entry for each point
        for point in sorted(self.points):
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill=tk.X, padx=10, pady=2)
            
            # Checkbox
            var = tk.BooleanVar(value=point in self.existing)
            cb = ttk.Checkbutton(frame, variable=var)
            cb.pack(side=tk.LEFT)
            
            # Label
            lbl = ttk.Label(frame, text=point, width=15)
            lbl.pack(side=tk.LEFT)
            
            # Entry
            entry = ttk.Entry(frame, width=15)
            entry.pack(side=tk.LEFT, padx=5)
            
            if point in self.existing:
                entry.insert(0, f"{self.existing[point]:.5f}")
            
            self.entries[point] = (var, entry)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=5)
    
    def _on_ok(self):
        self.result = {}
        for point, (var, entry) in self.entries.items():
            if var.get():
                try:
                    height = float(entry.get())
                    self.result[point] = height
                except ValueError:
                    messagebox.showerror("Error", f"Invalid height for {point}")
                    return
        self.destroy()


class LineAdjustmentDialog(tk.Toplevel):
    """Dialog for single line adjustment."""
    
    def __init__(self, parent, line: LevelingLine):
        super().__init__(parent)
        self.title("Line Adjustment / תיאום קו")
        self.geometry("700x600")
        self.transient(parent)
        self.grab_set()
        
        self.line = line
        self.result = None
        
        self._create_widgets()
        self.center_on_parent(parent)
    
    def center_on_parent(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
    
    def _create_widgets(self):
        # Line info frame
        info_frame = ttk.LabelFrame(self, text="Line Information / מידע על הקו")
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        info_grid = ttk.Frame(info_frame)
        info_grid.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(info_grid, text="File:").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_grid, text=self.line.filename or "-").grid(row=0, column=1, sticky=tk.W, padx=10)
        
        ttk.Label(info_grid, text="Start Point:").grid(row=1, column=0, sticky=tk.W)
        ttk.Label(info_grid, text=self.line.start_point).grid(row=1, column=1, sticky=tk.W, padx=10)
        
        ttk.Label(info_grid, text="End Point:").grid(row=2, column=0, sticky=tk.W)
        ttk.Label(info_grid, text=self.line.end_point).grid(row=2, column=1, sticky=tk.W, padx=10)
        
        ttk.Label(info_grid, text="Setups:").grid(row=3, column=0, sticky=tk.W)
        ttk.Label(info_grid, text=str(len(self.line.setups))).grid(row=3, column=1, sticky=tk.W, padx=10)
        
        ttk.Label(info_grid, text="Distance:").grid(row=4, column=0, sticky=tk.W)
        ttk.Label(info_grid, text=f"{self.line.total_distance:.2f} m").grid(row=4, column=1, sticky=tk.W, padx=10)
        
        ttk.Label(info_grid, text="Measured dH:").grid(row=5, column=0, sticky=tk.W)
        ttk.Label(info_grid, text=f"{self.line.total_height_diff:.5f} m").grid(row=5, column=1, sticky=tk.W, padx=10)
        
        # Input frame
        input_frame = ttk.LabelFrame(self, text="Benchmark Heights / גבהי נ\"צ")
        input_frame.pack(fill=tk.X, padx=10, pady=5)
        
        input_grid = ttk.Frame(input_frame)
        input_grid.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(input_grid, text=f"Start ({self.line.start_point}) Height:").grid(row=0, column=0, sticky=tk.W)
        self.start_height_entry = ttk.Entry(input_grid, width=15)
        self.start_height_entry.grid(row=0, column=1, padx=10)
        ttk.Label(input_grid, text="m").grid(row=0, column=2)
        
        ttk.Label(input_grid, text=f"End ({self.line.end_point}) Height:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.end_height_entry = ttk.Entry(input_grid, width=15)
        self.end_height_entry.grid(row=1, column=1, padx=10, pady=5)
        ttk.Label(input_grid, text="m").grid(row=1, column=2, pady=5)
        
        ttk.Label(input_grid, text="Leveling Class:").grid(row=2, column=0, sticky=tk.W)
        self.class_var = tk.StringVar(value="3")
        class_combo = ttk.Combobox(input_grid, textvariable=self.class_var, values=["1", "2", "3", "4"], width=5)
        class_combo.grid(row=2, column=1, sticky=tk.W, padx=10)
        
        # Calculate button
        ttk.Button(input_frame, text="Calculate Adjustment / חשב תיאום", 
                   command=self._calculate).pack(pady=10)
        
        # Results frame
        results_frame = ttk.LabelFrame(self, text="Results / תוצאות")
        results_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.results_text = scrolledtext.ScrolledText(results_frame, height=15, font=('Consolas', 10))
        self.results_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="Export / ייצוא", command=self._export).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Close / סגור", command=self.destroy).pack(side=tk.RIGHT, padx=5)
    
    def _calculate(self):
        try:
            start_height = float(self.start_height_entry.get())
            end_height = float(self.end_height_entry.get())
            leveling_class = int(self.class_var.get())
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric heights")
            return
        
        # Create benchmarks
        start_bm = Benchmark(point_id=self.line.start_point, height=start_height)
        end_bm = Benchmark(point_id=self.line.end_point, height=end_height)
        
        # Perform adjustment
        adjuster = LineAdjuster()
        adjuster.leveling_class = leveling_class
        
        try:
            adjusted_line, info = adjuster.adjust(self.line, start_bm, end_bm)
            self.result = (adjusted_line, info)
            self._display_results(info, start_height, end_height, leveling_class)
        except Exception as e:
            messagebox.showerror("Error", f"Adjustment failed: {str(e)}")
    
    def _display_results(self, info: dict, start_h: float, end_h: float, lev_class: int):
        self.results_text.delete('1.0', tk.END)
        
        expected_dh = end_h - start_h
        measured_dh = self.line.total_height_diff
        misclosure = info.get('misclosure_mm', (measured_dh - expected_dh) * 1000)
        tolerance = info.get('tolerance_mm', calculate_tolerance(self.line.total_distance, lev_class))
        within_tol = info.get('within_tolerance', abs(misclosure) <= tolerance)
        
        self.results_text.insert(tk.END, "=" * 60 + "\n")
        self.results_text.insert(tk.END, "LINE ADJUSTMENT RESULTS / תוצאות תיאום קו\n")
        self.results_text.insert(tk.END, "=" * 60 + "\n\n")
        
        self.results_text.insert(tk.END, f"Start Point:     {self.line.start_point}\n")
        self.results_text.insert(tk.END, f"Start Height:    {start_h:.5f} m (fixed)\n\n")
        
        self.results_text.insert(tk.END, f"End Point:       {self.line.end_point}\n")
        self.results_text.insert(tk.END, f"End Height:      {end_h:.5f} m (fixed)\n\n")
        
        self.results_text.insert(tk.END, f"Expected dH:     {expected_dh:.5f} m\n")
        self.results_text.insert(tk.END, f"Measured dH:     {measured_dh:.5f} m\n")
        self.results_text.insert(tk.END, f"Misclosure:      {misclosure:.3f} mm\n\n")
        
        self.results_text.insert(tk.END, f"Leveling Class:  {lev_class}\n")
        self.results_text.insert(tk.END, f"Tolerance:       ±{tolerance:.3f} mm\n")
        
        status = "✓ WITHIN TOLERANCE" if within_tol else "✗ EXCEEDS TOLERANCE"
        self.results_text.insert(tk.END, f"Status:          {status}\n\n")
        
        self.results_text.insert(tk.END, "-" * 60 + "\n")
        self.results_text.insert(tk.END, "INTERMEDIATE HEIGHTS / גבהים ביניים\n")
        self.results_text.insert(tk.END, "-" * 60 + "\n\n")
        
        # Calculate intermediate heights
        intermediate = info.get('intermediate_heights', {})
        corrections = info.get('corrections', [])
        
        if intermediate:
            self.results_text.insert(tk.END, f"{'Point':<15} {'Height (m)':<15} {'Correction (mm)':<15}\n")
            self.results_text.insert(tk.END, "-" * 45 + "\n")
            
            for point, height in intermediate.items():
                self.results_text.insert(tk.END, f"{point:<15} {height:.5f}\n")
        else:
            # Calculate manually if not provided
            current_height = start_h
            correction_per_setup = -misclosure / len(self.line.setups) / 1000  # in meters
            
            self.results_text.insert(tk.END, f"{'Point':<15} {'Adj Height (m)':<15} {'Correction (mm)':<15}\n")
            self.results_text.insert(tk.END, "-" * 45 + "\n")
            self.results_text.insert(tk.END, f"{self.line.start_point:<15} {start_h:.5f} (fixed)\n")
            
            for i, setup in enumerate(self.line.setups):
                current_height += setup.height_diff + correction_per_setup
                corr_mm = correction_per_setup * 1000
                point_name = setup.foresight_point or f"TP{i+1}"
                self.results_text.insert(tk.END, f"{point_name:<15} {current_height:.5f}        {corr_mm:+.3f}\n")
        
        self.results_text.insert(tk.END, "\n")
        self.results_text.insert(tk.END, f"Total distance:  {self.line.total_distance:.2f} m\n")
        self.results_text.insert(tk.END, f"Number of setups: {len(self.line.setups)}\n")
    
    def _export(self):
        if not self.result:
            messagebox.showinfo("Info", "Please calculate adjustment first")
            return
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"{self.line.filename}_adjustment.txt"
        )
        
        if filename:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(self.results_text.get('1.0', tk.END))
            messagebox.showinfo("Export", f"Results saved to:\n{filename}")


class NetworkAdjustmentDialog(tk.Toplevel):
    """Dialog for network least squares adjustment."""
    
    def __init__(self, parent, lines: List[LevelingLine]):
        super().__init__(parent)
        self.title("Network Adjustment (LSA) / תיאום רשת")
        self.geometry("900x700")
        self.transient(parent)
        self.grab_set()
        
        self.lines = lines
        self.result = None
        self.fixed_points = {}
        self.point_entries = {}

        # Visualization attributes
        self.current_figure = None
        self.canvas = None
        self.plot_type_var = None

        # Collect all unique points
        self.all_points = set()
        for line in lines:
            self.all_points.add(line.start_point)
            self.all_points.add(line.end_point)

        self._create_widgets()
        self.center_on_parent(parent)
    
    def center_on_parent(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
    
    def _create_widgets(self):
        # Main paned window
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left panel - Network info and fixed points
        left_frame = ttk.Frame(paned, width=350)
        paned.add(left_frame, weight=1)
        
        # Network summary
        summary_frame = ttk.LabelFrame(left_frame, text="Network Summary / סיכום רשת")
        summary_frame.pack(fill=tk.X, padx=5, pady=5)
        
        total_dist = sum(line.total_distance for line in self.lines)
        ttk.Label(summary_frame, text=f"Lines: {len(self.lines)}").pack(anchor=tk.W, padx=10)
        ttk.Label(summary_frame, text=f"Points: {len(self.all_points)}").pack(anchor=tk.W, padx=10)
        ttk.Label(summary_frame, text=f"Total Distance: {total_dist:.2f} m").pack(anchor=tk.W, padx=10, pady=(0, 5))
        
        # Observations list
        obs_frame = ttk.LabelFrame(left_frame, text="Observations / תצפיות")
        obs_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        obs_tree = ttk.Treeview(obs_frame, columns=('From', 'To', 'dH', 'Dist'), show='headings', height=8)
        obs_tree.heading('From', text='From')
        obs_tree.heading('To', text='To')
        obs_tree.heading('dH', text='dH (m)')
        obs_tree.heading('Dist', text='Dist (m)')
        
        obs_tree.column('From', width=70)
        obs_tree.column('To', width=70)
        obs_tree.column('dH', width=80)
        obs_tree.column('Dist', width=70)
        
        for line in self.lines:
            obs_tree.insert('', tk.END, values=(
                line.start_point, line.end_point,
                f"{line.total_height_diff:.5f}",
                f"{line.total_distance:.1f}"
            ))
        
        obs_scrollbar = ttk.Scrollbar(obs_frame, orient=tk.VERTICAL, command=obs_tree.yview)
        obs_tree.configure(yscrollcommand=obs_scrollbar.set)
        obs_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        obs_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Fixed points frame
        fixed_frame = ttk.LabelFrame(left_frame, text="Fixed Points / נקודות קבועות")
        fixed_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        ttk.Label(fixed_frame, text="Select fixed points and enter heights:").pack(anchor=tk.W, padx=5, pady=2)
        
        # Scrollable frame for points
        canvas = tk.Canvas(fixed_frame, height=150)
        scrollbar = ttk.Scrollbar(fixed_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        for point in sorted(self.all_points):
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill=tk.X, padx=5, pady=1)
            
            var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(frame, variable=var, width=3)
            cb.pack(side=tk.LEFT)
            
            lbl = ttk.Label(frame, text=point, width=12)
            lbl.pack(side=tk.LEFT)
            
            entry = ttk.Entry(frame, width=12)
            entry.pack(side=tk.LEFT, padx=5)
            
            self.point_entries[point] = (var, entry)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Auto-select benchmarks button
        btn_auto = ttk.Button(fixed_frame, text="Select All Benchmarks / בחר נ\"צ", 
                              command=self._auto_select_benchmarks)
        btn_auto.pack(pady=5)
        
        # Run adjustment button
        ttk.Button(left_frame, text="Run Adjustment / הרץ תיאום", 
                   command=self._run_adjustment).pack(pady=10)
        
        # Right panel - Notebook with Results and Visualization tabs
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=2)

        # Create notebook for tabs
        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 1: Results
        results_tab = ttk.Frame(self.notebook)
        self.notebook.add(results_tab, text="Results / תוצאות")

        results_frame = ttk.LabelFrame(results_tab, text="Adjustment Results / תוצאות תיאום")
        results_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.results_text = scrolledtext.ScrolledText(results_frame, font=('Consolas', 10))
        self.results_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 2: Visualization
        self._create_visualization_tab()
        
        # Bottom buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="Export FA1 / ייצוא", command=self._export_fa1).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Export TXT", command=self._export_txt).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Close / סגור", command=self.destroy).pack(side=tk.RIGHT, padx=5)
    
    def _auto_select_benchmarks(self):
        """Auto-select points that appear to be benchmarks (contain letters)."""
        for point, (var, entry) in self.point_entries.items():
            if is_benchmark(point):
                var.set(True)
    
    def _run_adjustment(self):
        # Collect fixed points
        self.fixed_points = {}
        for point, (var, entry) in self.point_entries.items():
            if var.get():
                try:
                    height = float(entry.get())
                    self.fixed_points[point] = height
                except ValueError:
                    messagebox.showerror("Error", f"Invalid height for {point}")
                    return
        
        if len(self.fixed_points) < 1:
            messagebox.showerror("Error", "Please select at least one fixed point with a known height")
            return
        
        try:
            adjuster = LeastSquaresAdjuster(max_iterations=10, tolerance=1e-8)
            self.result = adjuster.adjust_from_lines(self.lines, self.fixed_points)
            self._display_results()
        except Exception as e:
            messagebox.showerror("Error", f"Adjustment failed: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def _display_results(self):
        if not self.result:
            return
        
        self.results_text.delete('1.0', tk.END)
        
        total_dist = sum(line.total_distance for line in self.lines)
        
        self.results_text.insert(tk.END, "=" * 70 + "\n")
        self.results_text.insert(tk.END, "LEAST SQUARES ADJUSTMENT RESULTS / תוצאות תיאום מרובע פחות\n")
        self.results_text.insert(tk.END, "=" * 70 + "\n\n")
        
        self.results_text.insert(tk.END, f"Iterations:          {self.result.iteration}\n")
        self.results_text.insert(tk.END, f"M.S.E. Unit Weight:  {self.result.mse_unit_weight:.6f}\n")
        self.results_text.insert(tk.END, f"Total Distance:      {total_dist:.2f} m ({total_dist/1000:.3f} km)\n")
        self.results_text.insert(tk.END, f"K Coefficient:       {self.result.k_coefficient:.2f}\n\n")
        
        # Determine class
        k = self.result.k_coefficient
        if k <= 3:
            class_text = "Class 1 (First Order)"
        elif k <= 5:
            class_text = "Class 2 (Second Order)"
        elif k <= 10:
            class_text = "Class 3 (Third Order)"
        elif k <= 20:
            class_text = "Class 4 (Fourth Order)"
        else:
            class_text = "Exceeds Class 4"
        
        self.results_text.insert(tk.END, f"Classification:      {class_text}\n\n")
        
        self.results_text.insert(tk.END, "-" * 70 + "\n")
        self.results_text.insert(tk.END, "ADJUSTED HEIGHTS / גבהים מתואמים\n")
        self.results_text.insert(tk.END, "-" * 70 + "\n\n")
        
        self.results_text.insert(tk.END, f"{'No.':<5} {'Point':<15} {'Adjusted (m)':<15} {'M.S.E. (m)':<12} {'Status':<10}\n")
        self.results_text.insert(tk.END, "-" * 57 + "\n")
        
        for i, (point, height) in enumerate(sorted(self.result.adjusted_heights.items()), 1):
            mse = self.result.mse_heights.get(point, 0.0)
            status = "FIXED" if point in self.fixed_points else ""
            self.results_text.insert(tk.END, f"{i:<5} {point:<15} {height:>12.5f}   {mse:>10.6f}   {status}\n")
        
        self.results_text.insert(tk.END, "\n")
        self.results_text.insert(tk.END, "-" * 70 + "\n")
        self.results_text.insert(tk.END, "OBSERVATION RESIDUALS / שאריות תצפיות\n")
        self.results_text.insert(tk.END, "-" * 70 + "\n\n")
        
        self.results_text.insert(tk.END, f"{'From':<12} {'To':<12} {'Measured dH':<14} {'Residual (mm)':<14}\n")
        self.results_text.insert(tk.END, "-" * 52 + "\n")
        
        for line in self.lines:
            key = f"{line.start_point}-{line.end_point}"
            residual = self.result.residuals.get(key, 0.0) * 1000  # Convert to mm
            self.results_text.insert(tk.END, 
                f"{line.start_point:<12} {line.end_point:<12} {line.total_height_diff:>12.5f}   {residual:>+10.3f}\n")
        
        self.results_text.insert(tk.END, "\n" + "=" * 70 + "\n")

    def _create_visualization_tab(self):
        """Create the visualization tab for plotting results."""
        viz_tab = ttk.Frame(self.notebook)
        self.notebook.add(viz_tab, text="Visualization / ויזואליזציה")

        # Info frame
        info_frame = ttk.Frame(viz_tab)
        info_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        ttk.Label(
            info_frame,
            text="Visualization Tab",
            font=('Arial', 14, 'bold')
        ).pack(pady=10)

        ttk.Label(
            info_frame,
            text="After running adjustment, visualization will be displayed here.",
            font=('Arial', 10)
        ).pack(pady=5)

        # Placeholder for matplotlib canvas
        self.viz_canvas = None
        self.viz_tab_frame = viz_tab

    def _export_fa1(self):
        if not self.result:
            messagebox.showinfo("Info", "Please run adjustment first")
            return
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".FA1",
            filetypes=[("FA1 files", "*.FA1"), ("All files", "*.*")],
            initialfile="network_adjustment.FA1"
        )
        
        if filename:
            try:
                from exporters import FA1Exporter
                exporter = FA1Exporter()
                exporter.export(self.result, self.lines, filename)
                messagebox.showinfo("Export", f"FA1 file saved to:\n{filename}")
            except Exception as e:
                # Fallback - save as text
                with open(filename, 'w', encoding='cp1255', errors='replace') as f:
                    f.write(self.results_text.get('1.0', tk.END))
                messagebox.showinfo("Export", f"Results saved to:\n{filename}")
    
    def _export_txt(self):
        if not self.result:
            messagebox.showinfo("Info", "Please run adjustment first")
            return
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="network_adjustment.txt"
        )
        
        if filename:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(self.results_text.get('1.0', tk.END))
            messagebox.showinfo("Export", f"Results saved to:\n{filename}")


class EnhancedNetworkAdjustmentDialog(tk.Toplevel):
    """
    Enhanced dialog for network least squares adjustment with advanced features.

    Provides a comprehensive interface for network adjustment with:
    - Method selection (Parametric/Conditional)
    - Fixed points configuration
    - Advanced options (tolerance, iterations, weights)
    - Multi-tab results display
    - Visualization capabilities
    """

    def __init__(self, parent, lines: List[LevelingLine]):
        """
        Initialize the enhanced network adjustment dialog.

        Args:
            parent: Parent tkinter window
            lines: List of LevelingLine objects to adjust
        """
        super().__init__(parent)
        self.title("Enhanced Network Adjustment / תיאום רשת משופר")
        self.geometry("1400x800")
        self.transient(parent)
        self.grab_set()

        # Data
        self.lines = lines
        self.result = None
        self.fixed_points = {}

        # Collect all unique points
        self.all_points = set()
        for line in lines:
            self.all_points.add(line.start_point)
            self.all_points.add(line.end_point)

        # Control variables
        self.method_var = tk.StringVar(value="parametric")
        self.tolerance_var = tk.DoubleVar(value=1e-8)
        self.max_iterations_var = tk.IntVar(value=10)
        self.use_weights_var = tk.BooleanVar(value=True)
        self.auto_detect_outliers_var = tk.BooleanVar(value=False)
        self.show_intermediate_var = tk.BooleanVar(value=False)

        # Widget references
        self.fixed_points_tree = None
        self.summary_text = None
        self.adjusted_heights_tree = None
        self.residuals_tree = None
        self.viz_canvas = None
        self.matrix_text = None
        self.results_notebook = None

        # Create UI
        self._create_widgets()
        self.center_on_parent(parent)

    def center_on_parent(self, parent):
        """Center dialog on parent window."""
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _create_widgets(self):
        """Create the main dialog layout."""
        # Main horizontal paned window
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left panel - Configuration
        left_panel = ttk.Frame(main_paned, width=400)
        main_paned.add(left_panel, weight=1)
        self._create_left_panel(left_panel)

        # Right panel - Results
        right_panel = ttk.Frame(main_paned, width=1000)
        main_paned.add(right_panel, weight=2)
        self._create_right_panel(right_panel)

    def _create_left_panel(self, parent: ttk.Frame):
        """
        Create the left configuration panel.

        Contains:
        - Method selection
        - Fixed points table
        - Advanced options
        - Action buttons
        """
        # Method selection frame
        method_frame = ttk.LabelFrame(parent, text="Adjustment Method / שיטת תיאום")
        method_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Radiobutton(
            method_frame,
            text="Parametric (Observations)",
            variable=self.method_var,
            value="parametric"
        ).pack(anchor=tk.W, padx=10, pady=2)

        ttk.Radiobutton(
            method_frame,
            text="Conditional (Constraints)",
            variable=self.method_var,
            value="conditional"
        ).pack(anchor=tk.W, padx=10, pady=2)

        ttk.Label(
            method_frame,
            text="Note: Both methods yield equivalent results",
            font=('Arial', 8, 'italic')
        ).pack(anchor=tk.W, padx=10, pady=(0, 5))

        # Fixed points frame
        fixed_frame = ttk.LabelFrame(parent, text="Fixed Points / נקודות קבועות")
        fixed_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Toolbar for fixed points
        fixed_toolbar = ttk.Frame(fixed_frame)
        fixed_toolbar.pack(fill=tk.X, padx=5, pady=2)

        ttk.Button(
            fixed_toolbar,
            text="Auto-Select Benchmarks",
            command=self._auto_select_benchmarks
        ).pack(side=tk.LEFT, padx=2)

        ttk.Button(
            fixed_toolbar,
            text="Clear All",
            command=self._clear_fixed_points
        ).pack(side=tk.LEFT, padx=2)

        # Fixed points table
        columns = ('Point', 'Height', 'Std Dev')
        self.fixed_points_tree = ttk.Treeview(
            fixed_frame,
            columns=columns,
            show='tree headings',
            height=12
        )

        self.fixed_points_tree.heading('#0', text='Use')
        self.fixed_points_tree.heading('Point', text='Point')
        self.fixed_points_tree.heading('Height', text='Height (m)')
        self.fixed_points_tree.heading('Std Dev', text='Std Dev (m)')

        self.fixed_points_tree.column('#0', width=40)
        self.fixed_points_tree.column('Point', width=100)
        self.fixed_points_tree.column('Height', width=120)
        self.fixed_points_tree.column('Std Dev', width=100)

        # Populate with all points
        for point in sorted(self.all_points):
            self.fixed_points_tree.insert(
                '',
                tk.END,
                text='☐',
                values=(point, '', '0.000'),
                tags=('unchecked',)
            )

        # Bind events
        self.fixed_points_tree.bind('<Double-1>', self._on_fixed_point_double_click)

        fixed_scroll = ttk.Scrollbar(
            fixed_frame,
            orient=tk.VERTICAL,
            command=self.fixed_points_tree.yview
        )
        self.fixed_points_tree.configure(yscrollcommand=fixed_scroll.set)

        self.fixed_points_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fixed_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Advanced options frame
        options_frame = ttk.LabelFrame(parent, text="Advanced Options / אפשרויות מתקדמות")
        options_frame.pack(fill=tk.X, padx=5, pady=5)

        # Tolerance
        tol_frame = ttk.Frame(options_frame)
        tol_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(tol_frame, text="Convergence Tolerance:").pack(side=tk.LEFT)
        ttk.Spinbox(
            tol_frame,
            from_=1e-10,
            to=1e-4,
            increment=1e-9,
            textvariable=self.tolerance_var,
            width=12,
            format="%.1e"
        ).pack(side=tk.RIGHT)

        # Max iterations
        iter_frame = ttk.Frame(options_frame)
        iter_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(iter_frame, text="Max Iterations:").pack(side=tk.LEFT)
        ttk.Spinbox(
            iter_frame,
            from_=1,
            to=100,
            textvariable=self.max_iterations_var,
            width=12
        ).pack(side=tk.RIGHT)

        # Checkboxes
        ttk.Checkbutton(
            options_frame,
            text="Use distance-based weights",
            variable=self.use_weights_var
        ).pack(anchor=tk.W, padx=5, pady=2)

        ttk.Checkbutton(
            options_frame,
            text="Auto-detect outliers",
            variable=self.auto_detect_outliers_var
        ).pack(anchor=tk.W, padx=5, pady=2)

        ttk.Checkbutton(
            options_frame,
            text="Show intermediate iterations",
            variable=self.show_intermediate_var
        ).pack(anchor=tk.W, padx=5, pady=2)

        # Action buttons
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill=tk.X, padx=5, pady=10)

        ttk.Button(
            button_frame,
            text="Run Adjustment",
            command=self._run_adjustment,
            style='Accent.TButton'
        ).pack(fill=tk.X, pady=2)

        ttk.Button(
            button_frame,
            text="Clear Results",
            command=self._clear_results
        ).pack(fill=tk.X, pady=2)

        # Export section
        ttk.Separator(button_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        ttk.Label(button_frame, text="Export:", font=('Arial', 9, 'bold')).pack(anchor=tk.W)

        export_buttons_frame = ttk.Frame(button_frame)
        export_buttons_frame.pack(fill=tk.X, pady=2)

        ttk.Button(
            export_buttons_frame,
            text="TXT",
            command=self._export_txt,
            width=8
        ).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        ttk.Button(
            export_buttons_frame,
            text="CSV",
            command=self._export_csv,
            width=8
        ).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        ttk.Button(
            export_buttons_frame,
            text="Plot",
            command=self._export_plot,
            width=8
        ).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        ttk.Separator(button_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        ttk.Button(
            button_frame,
            text="Close",
            command=self.destroy
        ).pack(fill=tk.X, pady=2)

    def _create_right_panel(self, parent: ttk.Frame):
        """
        Create the right results panel.

        Contains tabbed interface with:
        - Summary
        - Adjusted Heights
        - Residuals
        - Visualization
        - Matrix Diagnostics
        """
        # Results notebook
        self.results_notebook = ttk.Notebook(parent)
        self.results_notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Summary
        summary_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(summary_tab, text="Summary / סיכום")
        self._create_summary_tab(summary_tab)

        # Tab 2: Adjusted Heights
        heights_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(heights_tab, text="Adjusted Heights / גבהים מתואמים")
        self._create_heights_tab(heights_tab)

        # Tab 3: Residuals
        residuals_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(residuals_tab, text="Residuals / שאריות")
        self._create_residuals_tab(residuals_tab)

        # Tab 4: Visualization
        viz_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(viz_tab, text="Visualization / ויזואליזציה")
        self._create_visualization_tab(viz_tab)

        # Tab 5: Matrix Diagnostics
        matrix_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(matrix_tab, text="Matrix Diagnostics / אבחון מטריצות")
        self._create_matrix_tab(matrix_tab)

    def _create_summary_tab(self, parent: ttk.Frame):
        """Create the summary tab with quality metrics."""
        self.summary_text = scrolledtext.ScrolledText(
            parent,
            font=('Consolas', 10),
            wrap=tk.WORD
        )
        self.summary_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Initial message
        self.summary_text.insert(tk.END, "No adjustment results yet.\n\n")
        self.summary_text.insert(tk.END, "Configure fixed points and click 'Run Adjustment'.")

    def _create_heights_tab(self, parent: ttk.Frame):
        """Create the adjusted heights table tab."""
        columns = ('No', 'Point', 'Adjusted Height', 'Std Error', 'Status')
        self.adjusted_heights_tree = ttk.Treeview(
            parent,
            columns=columns,
            show='headings'
        )

        self.adjusted_heights_tree.heading('No', text='No.')
        self.adjusted_heights_tree.heading('Point', text='Point')
        self.adjusted_heights_tree.heading('Adjusted Height', text='Adjusted Height (m)')
        self.adjusted_heights_tree.heading('Std Error', text='Std Error (m)')
        self.adjusted_heights_tree.heading('Status', text='Status')

        self.adjusted_heights_tree.column('No', width=50)
        self.adjusted_heights_tree.column('Point', width=150)
        self.adjusted_heights_tree.column('Adjusted Height', width=150)
        self.adjusted_heights_tree.column('Std Error', width=150)
        self.adjusted_heights_tree.column('Status', width=100)

        heights_scroll = ttk.Scrollbar(
            parent,
            orient=tk.VERTICAL,
            command=self.adjusted_heights_tree.yview
        )
        self.adjusted_heights_tree.configure(yscrollcommand=heights_scroll.set)

        self.adjusted_heights_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        heights_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=5)

    def _create_residuals_tab(self, parent: ttk.Frame):
        """Create the residuals table tab."""
        columns = ('From', 'To', 'Observed dH', 'Adjusted dH', 'Residual', 'Std Residual')
        self.residuals_tree = ttk.Treeview(
            parent,
            columns=columns,
            show='headings'
        )

        self.residuals_tree.heading('From', text='From')
        self.residuals_tree.heading('To', text='To')
        self.residuals_tree.heading('Observed dH', text='Observed dH (m)')
        self.residuals_tree.heading('Adjusted dH', text='Adjusted dH (m)')
        self.residuals_tree.heading('Residual', text='Residual (mm)')
        self.residuals_tree.heading('Std Residual', text='Std Residual')

        self.residuals_tree.column('From', width=100)
        self.residuals_tree.column('To', width=100)
        self.residuals_tree.column('Observed dH', width=130)
        self.residuals_tree.column('Adjusted dH', width=130)
        self.residuals_tree.column('Residual', width=120)
        self.residuals_tree.column('Std Residual', width=120)

        residuals_scroll = ttk.Scrollbar(
            parent,
            orient=tk.VERTICAL,
            command=self.residuals_tree.yview
        )
        self.residuals_tree.configure(yscrollcommand=residuals_scroll.set)

        self.residuals_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        residuals_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=5)

    def _create_visualization_tab(self, parent: ttk.Frame):
        """Create the visualization tab (matplotlib canvas placeholder)."""
        # Placeholder frame
        placeholder_frame = ttk.Frame(parent)
        placeholder_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        ttk.Label(
            placeholder_frame,
            text="Network Visualization",
            font=('Arial', 14, 'bold')
        ).pack(pady=10)

        ttk.Label(
            placeholder_frame,
            text="Matplotlib canvas will be integrated here",
            font=('Arial', 10)
        ).pack(pady=5)

        ttk.Label(
            placeholder_frame,
            text="Features to be implemented:",
            font=('Arial', 10, 'bold')
        ).pack(pady=(20, 5))

        features = [
            "- Network topology graph",
            "- Point height visualization",
            "- Residual distribution plot",
            "- Error ellipses",
            "- Quality heat map"
        ]

        for feature in features:
            ttk.Label(
                placeholder_frame,
                text=feature,
                font=('Arial', 9)
            ).pack(anchor=tk.W, padx=50)

    def _create_matrix_tab(self, parent: ttk.Frame):
        """Create the matrix diagnostics tab (placeholder)."""
        self.matrix_text = scrolledtext.ScrolledText(
            parent,
            font=('Consolas', 9),
            wrap=tk.WORD
        )
        self.matrix_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Initial message
        self.matrix_text.insert(tk.END, "Matrix Diagnostics\n")
        self.matrix_text.insert(tk.END, "=" * 60 + "\n\n")
        self.matrix_text.insert(tk.END, "Information to be displayed:\n\n")
        self.matrix_text.insert(tk.END, "- Normal matrix condition number\n")
        self.matrix_text.insert(tk.END, "- Matrix rank and deficiency\n")
        self.matrix_text.insert(tk.END, "- Eigenvalue analysis\n")
        self.matrix_text.insert(tk.END, "- Numerical stability indicators\n")
        self.matrix_text.insert(tk.END, "- Correlation matrix summary\n")

    # Fixed Points Table Interaction Methods

    def _auto_select_benchmarks(self):
        """Auto-select points that appear to be benchmarks based on naming conventions."""
        # Common benchmark naming patterns: BM, RP, TBM, etc.
        benchmark_patterns = ['BM', 'RP', 'TBM', 'CP', 'STA']

        selected_count = 0
        for item in self.fixed_points_tree.get_children():
            point_name = self.fixed_points_tree.item(item, 'values')[0]

            # Check if point name matches benchmark patterns
            is_bm = any(pattern in str(point_name).upper() for pattern in benchmark_patterns)

            if is_bm:
                # Mark as checked
                self.fixed_points_tree.item(item, text='☑', tags=('checked',))
                selected_count += 1

        if selected_count > 0:
            messagebox.showinfo(
                "Auto-Select Complete",
                f"Selected {selected_count} potential benchmark(s).\n\n"
                "Double-click on points to enter heights."
            )
        else:
            messagebox.showinfo(
                "Auto-Select",
                "No benchmark patterns detected.\n\n"
                "Please manually select and configure fixed points."
            )

    def _clear_fixed_points(self):
        """Clear all fixed point selections and heights."""
        for item in self.fixed_points_tree.get_children():
            self.fixed_points_tree.item(item, text='☐', tags=('unchecked',))
            values = list(self.fixed_points_tree.item(item, 'values'))
            values[1] = ''  # Clear height
            values[2] = '0.000'  # Reset std dev
            self.fixed_points_tree.item(item, values=values)

        self.fixed_points.clear()
        messagebox.showinfo("Cleared", "All fixed points have been cleared.")

    def _on_fixed_point_double_click(self, event):
        """Handle double-click on fixed point to toggle selection and edit height."""
        # Get clicked item
        item = self.fixed_points_tree.selection()
        if not item:
            return

        item = item[0]
        point_name = self.fixed_points_tree.item(item, 'values')[0]
        current_height = self.fixed_points_tree.item(item, 'values')[1]
        current_std = self.fixed_points_tree.item(item, 'values')[2]
        is_checked = self.fixed_points_tree.item(item, 'text') == '☑'

        # Determine click region
        region = self.fixed_points_tree.identify_region(event.x, event.y)
        column = self.fixed_points_tree.identify_column(event.x)

        # If clicked on checkbox column, toggle check state
        if column == '#0':
            if is_checked:
                self.fixed_points_tree.item(item, text='☐', tags=('unchecked',))
                if point_name in self.fixed_points:
                    del self.fixed_points[point_name]
            else:
                self.fixed_points_tree.item(item, text='☑', tags=('checked',))
                # Prompt for height if not set
                if not current_height or current_height == '':
                    self._prompt_for_height(item, point_name)

        # If clicked on height or std dev column, edit value
        elif column in ('#2', '#3'):  # Height or Std Dev columns
            if column == '#2':  # Height column
                self._prompt_for_height(item, point_name)
            else:  # Std Dev column
                self._prompt_for_std_dev(item, point_name)

    def _prompt_for_height(self, item, point_name: str):
        """Prompt user to enter height for a fixed point."""
        current_height = self.fixed_points_tree.item(item, 'values')[1]

        # Create input dialog
        height_str = simpledialog.askstring(
            "Enter Height",
            f"Enter known height for point '{point_name}' (meters):",
            initialvalue=current_height if current_height else ''
        )

        if height_str is not None:
            try:
                height = float(height_str)
                values = list(self.fixed_points_tree.item(item, 'values'))
                values[1] = f"{height:.4f}"
                self.fixed_points_tree.item(item, values=values)

                # Update fixed points dictionary
                std_dev = float(values[2])
                self.fixed_points[point_name] = (height, std_dev)

                # Ensure point is checked
                self.fixed_points_tree.item(item, text='☑', tags=('checked',))

            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid numeric height.")

    def _prompt_for_std_dev(self, item, point_name: str):
        """Prompt user to enter standard deviation for a fixed point."""
        current_std = self.fixed_points_tree.item(item, 'values')[2]

        # Create input dialog
        std_str = simpledialog.askstring(
            "Enter Standard Deviation",
            f"Enter standard deviation for point '{point_name}' (meters):",
            initialvalue=current_std if current_std else '0.000'
        )

        if std_str is not None:
            try:
                std_dev = float(std_str)
                if std_dev < 0:
                    messagebox.showerror("Invalid Input", "Standard deviation must be non-negative.")
                    return

                values = list(self.fixed_points_tree.item(item, 'values'))
                values[2] = f"{std_dev:.3f}"
                self.fixed_points_tree.item(item, values=values)

                # Update fixed points dictionary if this point is selected
                if point_name in self.fixed_points:
                    height = self.fixed_points[point_name][0]
                    self.fixed_points[point_name] = (height, std_dev)

            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid numeric standard deviation.")

    # Adjustment Execution Methods

    def _run_adjustment(self):
        """Run the network adjustment with current settings."""
        # Validate fixed points
        if not self.fixed_points:
            messagebox.showerror(
                "No Fixed Points",
                "Please select at least one fixed point with a known height."
            )
            return

        # Clear previous results
        self._clear_results()

        try:
            # Get adjustment method
            method = self.method_var.get()

            # Prepare adjustment parameters
            tolerance = self.tolerance_var.get()
            max_iter = self.max_iterations_var.get()
            use_weights = self.use_weights_var.get()

            # Run appropriate adjustment method
            if method == "parametric":
                self._run_parametric_adjustment(tolerance, max_iter, use_weights)
            else:  # conditional
                self._run_conditional_adjustment(tolerance, max_iter, use_weights)

            # Display results if successful
            if self.result:
                self._populate_summary()
                self._populate_heights_table()
                self._populate_residuals_table()
                self._populate_matrix_diagnostics()
                if MATPLOTLIB_AVAILABLE:
                    self._populate_visualization()

                messagebox.showinfo(
                    "Success",
                    "Network adjustment completed successfully!\n\n"
                    f"Method: {method.capitalize()}\n"
                    f"Adjusted points: {len(self.result.adjusted_heights)}\n"
                    f"σ₀: {self.result.mse_unit_weight:.4f}"
                )

        except InsufficientObservationsError as e:
            messagebox.showerror("Insufficient Observations", str(e))
        except SingularMatrixError as e:
            messagebox.showerror("Singular Matrix", str(e))
        except Exception as e:
            messagebox.showerror("Adjustment Error", f"An error occurred:\n\n{str(e)}")

    def _run_parametric_adjustment(self, tolerance: float, max_iter: int, use_weights: bool):
        """Run parametric (Ax+L) least squares adjustment."""
        from ..config.models import MeasurementSummary

        # Convert LevelingLine objects to MeasurementSummary objects
        observations = []
        for line in self.lines:
            if not line.is_used:
                continue

            obs = MeasurementSummary(
                from_point=line.start_point,
                to_point=line.end_point,
                height_diff=line.total_height_diff,
                distance=line.total_distance,
                num_setups=len(line.setups),
                bf_diff=0.0,  # Not used in LSA
                year_month="",
                source_file=line.filename,
                is_used=line.is_used
            )
            observations.append(obs)

        # Convert fixed_points from Dict[str, Tuple[float, float]] to Dict[str, float]
        fixed_heights_dict = {point: height for point, (height, std_dev) in self.fixed_points.items()}

        # Create adjuster
        adjuster = LeastSquaresAdjuster(
            max_iterations=max_iter,
            tolerance=tolerance,
            check_stability=True
        )

        # Run adjustment
        self.result = adjuster.adjust(observations, fixed_heights_dict)

    def _run_conditional_adjustment(self, tolerance: float, max_iter: int, use_weights: bool):
        """Run conditional (Bv+W) least squares adjustment."""
        from ..config.models import MeasurementSummary

        # Convert LevelingLine objects to MeasurementSummary objects
        observations = []
        for line in self.lines:
            if not line.is_used:
                continue

            obs = MeasurementSummary(
                from_point=line.start_point,
                to_point=line.end_point,
                height_diff=line.total_height_diff,
                distance=line.total_distance,
                num_setups=len(line.setups),
                bf_diff=0.0,
                year_month="",
                source_file=line.filename,
                is_used=line.is_used
            )
            observations.append(obs)

        # Convert fixed_points from Dict[str, Tuple[float, float]] to Dict[str, float]
        fixed_heights_dict = {point: height for point, (height, std_dev) in self.fixed_points.items()}

        # Create adjuster
        adjuster = ConditionalAdjuster(
            max_iterations=max_iter,
            tolerance=tolerance,
            check_stability=True
        )

        # Run adjustment
        self.result = adjuster.adjust(observations, fixed_heights_dict)

    def _clear_results(self):
        """Clear all results from the display."""
        # Clear summary text
        if self.summary_text:
            self.summary_text.delete('1.0', tk.END)
            self.summary_text.insert(tk.END, "No adjustment results yet.\n\n")
            self.summary_text.insert(tk.END, "Configure fixed points and click 'Run Adjustment'.")

        # Clear heights table
        if self.adjusted_heights_tree:
            for item in self.adjusted_heights_tree.get_children():
                self.adjusted_heights_tree.delete(item)

        # Clear residuals table
        if self.residuals_tree:
            for item in self.residuals_tree.get_children():
                self.residuals_tree.delete(item)

        # Clear matrix diagnostics
        if self.matrix_text:
            self.matrix_text.delete('1.0', tk.END)
            self.matrix_text.insert(tk.END, "Matrix Diagnostics\n")
            self.matrix_text.insert(tk.END, "=" * 60 + "\n\n")
            self.matrix_text.insert(tk.END, "Run adjustment to see diagnostics.")

        # Clear visualization
        if self.viz_canvas:
            self.viz_canvas.get_tk_widget().destroy()
            self.viz_canvas = None

        # Reset result
        self.result = None

    # Results Display Population Methods

    def _populate_summary(self):
        """Populate the summary tab with adjustment results."""
        if not self.result:
            return

        self.summary_text.delete('1.0', tk.END)

        # Header
        self.summary_text.insert(tk.END, "NETWORK ADJUSTMENT SUMMARY\n", 'header')
        self.summary_text.insert(tk.END, "=" * 60 + "\n\n")

        # Method
        method = "Parametric (Ax+L)" if self.method_var.get() == "parametric" else "Conditional (Bv+W)"
        self.summary_text.insert(tk.END, f"Method: {method}\n\n")

        # Statistics
        self.summary_text.insert(tk.END, "QUALITY METRICS:\n", 'subheader')
        self.summary_text.insert(tk.END, f"  Standard error of unit weight (σ₀): {self.result.mse_unit_weight:.6f}\n")
        self.summary_text.insert(tk.END, f"  K coefficient: {self.result.k_coefficient:.2f}\n")

        # Network information
        self.summary_text.insert(tk.END, f"\nNETWORK INFORMATION:\n", 'subheader')
        self.summary_text.insert(tk.END, f"  Total points: {len(self.result.adjusted_heights)}\n")
        self.summary_text.insert(tk.END, f"  Fixed points: {len(self.fixed_points)}\n")
        self.summary_text.insert(tk.END, f"  Unknown points: {len(self.result.adjusted_heights) - len(self.fixed_points)}\n")
        self.summary_text.insert(tk.END, f"  Total distance: {self.result.total_distance_km:.3f} km\n")

        # Convergence information
        self.summary_text.insert(tk.END, f"\nCONVERGENCE:\n", 'subheader')
        self.summary_text.insert(tk.END, f"  Iterations: {self.result.iteration}\n")

        # Configure tags for formatting
        self.summary_text.tag_config('header', font=('Consolas', 11, 'bold'))
        self.summary_text.tag_config('subheader', font=('Consolas', 10, 'bold'))

    def _populate_heights_table(self):
        """Populate the adjusted heights table."""
        if not self.result:
            return

        # Clear existing items
        for item in self.adjusted_heights_tree.get_children():
            self.adjusted_heights_tree.delete(item)

        # Populate with adjusted heights
        for idx, (point, height) in enumerate(sorted(self.result.adjusted_heights.items()), 1):
            # Get mean square error (MSE)
            mse = self.result.mse_heights.get(point, 0.0)

            # Determine status
            if point in self.fixed_points:
                status = "Fixed"
                tag = 'fixed'
            else:
                status = "Adjusted"
                tag = 'adjusted'

            self.adjusted_heights_tree.insert(
                '',
                tk.END,
                values=(idx, point, f"{height:.4f}", f"{mse:.5f}", status),
                tags=(tag,)
            )

        # Configure tags
        self.adjusted_heights_tree.tag_configure('fixed', background='#e8f4f8')
        self.adjusted_heights_tree.tag_configure('adjusted', background='#ffffff')

    def _populate_residuals_table(self):
        """Populate the residuals table."""
        if not self.result:
            return

        # Clear existing items
        for item in self.residuals_tree.get_children():
            self.residuals_tree.delete(item)

        # Populate with residuals
        for line in self.lines:
            from_pt = line.start_point
            to_pt = line.end_point
            observed_dh = line.height_diff

            # Calculate adjusted dH
            if from_pt in self.result.adjusted_heights and to_pt in self.result.adjusted_heights:
                adjusted_dh = self.result.adjusted_heights[to_pt] - self.result.adjusted_heights[from_pt]
                residual = observed_dh - adjusted_dh
                residual_mm = residual * 1000  # Convert to mm

                # Calculate standardized residual if available
                std_residual = 0.0
                if hasattr(self.result, 'residuals') and line in self.result.residuals:
                    std_residual = self.result.residuals[line]

                # Determine tag based on residual magnitude
                if abs(residual_mm) > 3.0:
                    tag = 'warning'
                elif abs(residual_mm) > 2.0:
                    tag = 'caution'
                else:
                    tag = 'normal'

                self.residuals_tree.insert(
                    '',
                    tk.END,
                    values=(
                        from_pt,
                        to_pt,
                        f"{observed_dh:.4f}",
                        f"{adjusted_dh:.4f}",
                        f"{residual_mm:.2f}",
                        f"{std_residual:.2f}" if std_residual else "N/A"
                    ),
                    tags=(tag,)
                )

        # Configure tags
        self.residuals_tree.tag_configure('normal', background='#ffffff')
        self.residuals_tree.tag_configure('caution', background='#fff3cd')
        self.residuals_tree.tag_configure('warning', background='#f8d7da')

    def _populate_matrix_diagnostics(self):
        """Populate the matrix diagnostics tab."""
        if not self.result:
            return

        self.matrix_text.delete('1.0', tk.END)

        self.matrix_text.insert(tk.END, "MATRIX DIAGNOSTICS\n", 'header')
        self.matrix_text.insert(tk.END, "=" * 60 + "\n\n")

        # Matrix stability information
        if hasattr(self.result, 'condition_number'):
            self.matrix_text.insert(tk.END, "NUMERICAL STABILITY:\n", 'subheader')
            self.matrix_text.insert(tk.END, f"  Condition number: {self.result.condition_number:.2e}\n")

            # Interpret condition number
            if self.result.condition_number < 1e3:
                stability = "Excellent (well-conditioned)"
            elif self.result.condition_number < 1e6:
                stability = "Good"
            elif self.result.condition_number < 1e10:
                stability = "Fair (mildly ill-conditioned)"
            else:
                stability = "Poor (ill-conditioned)"

            self.matrix_text.insert(tk.END, f"  Assessment: {stability}\n\n")

        # Matrix rank information
        if hasattr(self.result, 'rank'):
            self.matrix_text.insert(tk.END, "MATRIX RANK:\n", 'subheader')
            self.matrix_text.insert(tk.END, f"  Rank: {self.result.rank}\n")
            if hasattr(self.result, 'expected_rank'):
                self.matrix_text.insert(tk.END, f"  Expected rank: {self.result.expected_rank}\n")
                if self.result.rank < self.result.expected_rank:
                    self.matrix_text.insert(tk.END, "  ⚠ Warning: Matrix is rank deficient!\n")
            self.matrix_text.insert(tk.END, "\n")

        # Additional diagnostics
        self.matrix_text.insert(tk.END, "ADDITIONAL INFORMATION:\n", 'subheader')
        self.matrix_text.insert(tk.END, f"  Adjustment method: {self.method_var.get().capitalize()}\n")
        self.matrix_text.insert(tk.END, f"  Convergence tolerance: {self.tolerance_var.get():.1e}\n")
        self.matrix_text.insert(tk.END, f"  Max iterations: {self.max_iterations_var.get()}\n")

        # Configure tags
        self.matrix_text.tag_config('header', font=('Consolas', 11, 'bold'))
        self.matrix_text.tag_config('subheader', font=('Consolas', 10, 'bold'))

    def _populate_visualization(self):
        """Create visualization of residuals using matplotlib."""
        if not self.result or not MATPLOTLIB_AVAILABLE:
            return

        # Get visualization tab
        viz_tab = self.results_notebook.nametowidget(
            self.results_notebook.tabs()[3]  # 4th tab (index 3)
        )

        # Clear existing canvas if any
        for widget in viz_tab.winfo_children():
            widget.destroy()

        # Create matplotlib figure
        fig = Figure(figsize=(10, 6), dpi=100)

        # Create two subplots: bar chart and histogram
        ax1 = fig.add_subplot(121)
        ax2 = fig.add_subplot(122)

        # Collect residuals
        residuals_mm = []
        line_labels = []

        for line in self.lines:
            from_pt = line.start_point
            to_pt = line.end_point
            observed_dh = line.height_diff

            if from_pt in self.result.adjusted_heights and to_pt in self.result.adjusted_heights:
                adjusted_dh = self.result.adjusted_heights[to_pt] - self.result.adjusted_heights[from_pt]
                residual = (observed_dh - adjusted_dh) * 1000  # mm
                residuals_mm.append(residual)
                line_labels.append(f"{from_pt}-{to_pt}")

        # Plot 1: Bar chart of residuals
        colors = ['red' if abs(r) > 3.0 else 'orange' if abs(r) > 2.0 else 'green'
                  for r in residuals_mm]
        ax1.bar(range(len(residuals_mm)), residuals_mm, color=colors, alpha=0.7)
        ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax1.axhline(y=2, color='orange', linestyle='--', linewidth=0.5, alpha=0.5)
        ax1.axhline(y=-2, color='orange', linestyle='--', linewidth=0.5, alpha=0.5)
        ax1.axhline(y=3, color='red', linestyle='--', linewidth=0.5, alpha=0.5)
        ax1.axhline(y=-3, color='red', linestyle='--', linewidth=0.5, alpha=0.5)
        ax1.set_xlabel('Observation Number')
        ax1.set_ylabel('Residual (mm)')
        ax1.set_title('Residuals by Observation')
        ax1.grid(True, alpha=0.3)

        # Plot 2: Histogram
        ax2.hist(residuals_mm, bins=20, color='steelblue', alpha=0.7, edgecolor='black')
        ax2.axvline(x=0, color='black', linestyle='-', linewidth=1)
        ax2.set_xlabel('Residual (mm)')
        ax2.set_ylabel('Frequency')
        ax2.set_title('Residual Distribution')
        ax2.grid(True, alpha=0.3)

        # Add statistics text
        import numpy as np
        mean_res = np.mean(residuals_mm)
        std_res = np.std(residuals_mm)
        ax2.text(
            0.95, 0.95,
            f'Mean: {mean_res:.2f} mm\nStd: {std_res:.2f} mm',
            transform=ax2.transAxes,
            verticalalignment='top',
            horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5)
        )

        fig.tight_layout()

        # Create canvas
        canvas = FigureCanvasTkAgg(fig, master=viz_tab)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Add toolbar
        toolbar_frame = ttk.Frame(viz_tab)
        toolbar_frame.pack(fill=tk.X)
        toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
        toolbar.update()

        self.viz_canvas = canvas

    # Export Methods

    def _export_txt(self):
        """Export adjustment results to TXT file."""
        if not self.result:
            messagebox.showinfo("No Results", "Please run adjustment first.")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="enhanced_network_adjustment.txt"
        )

        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    # Header
                    f.write("=" * 80 + "\n")
                    f.write("ENHANCED NETWORK ADJUSTMENT RESULTS\n")
                    f.write("=" * 80 + "\n\n")

                    # Method
                    method = "Parametric (Ax+L)" if self.method_var.get() == "parametric" else "Conditional (Bv+W)"
                    f.write(f"Adjustment Method: {method}\n\n")

                    # Quality metrics
                    f.write("QUALITY METRICS:\n")
                    f.write("-" * 80 + "\n")
                    f.write(f"  Standard error of unit weight (σ₀): {self.result.mse_unit_weight:.6f}\n")
                    f.write(f"  K coefficient: {self.result.k_coefficient:.2f}\n")
                    f.write("\n")

                    # Network information
                    f.write("NETWORK INFORMATION:\n")
                    f.write("-" * 80 + "\n")
                    f.write(f"  Total points: {len(self.result.adjusted_heights)}\n")
                    f.write(f"  Fixed points: {len(self.fixed_points)}\n")
                    f.write(f"  Unknown points: {len(self.result.adjusted_heights) - len(self.fixed_points)}\n")
                    f.write(f"  Observations: {len(self.lines)}\n")
                    if hasattr(self.result, 'redundancy'):
                        f.write(f"  Degrees of freedom: {self.result.redundancy}\n")
                    f.write("\n")

                    # Adjusted heights
                    f.write("ADJUSTED HEIGHTS:\n")
                    f.write("-" * 80 + "\n")
                    f.write(f"{'No.':<6} {'Point':<15} {'Height (m)':<15} {'Std Error (m)':<15} {'Status':<10}\n")
                    f.write("-" * 80 + "\n")

                    for idx, (point, height) in enumerate(sorted(self.result.adjusted_heights.items()), 1):
                        mse = self.result.mse_heights.get(point, 0.0)
                        status = "Fixed" if point in self.fixed_points else "Adjusted"
                        f.write(f"{idx:<6} {point:<15} {height:<15.4f} {mse:<15.5f} {status:<10}\n")

                    f.write("\n")

                    # Residuals
                    f.write("RESIDUALS:\n")
                    f.write("-" * 80 + "\n")
                    f.write(f"{'From':<10} {'To':<10} {'Obs dH (m)':<15} {'Adj dH (m)':<15} {'Residual (mm)':<15}\n")
                    f.write("-" * 80 + "\n")

                    for line in self.lines:
                        from_pt = line.start_point
                        to_pt = line.end_point
                        observed_dh = line.height_diff

                        if from_pt in self.result.adjusted_heights and to_pt in self.result.adjusted_heights:
                            adjusted_dh = self.result.adjusted_heights[to_pt] - self.result.adjusted_heights[from_pt]
                            residual_mm = (observed_dh - adjusted_dh) * 1000

                            f.write(f"{from_pt:<10} {to_pt:<10} {observed_dh:<15.4f} {adjusted_dh:<15.4f} {residual_mm:<15.2f}\n")

                    f.write("\n")
                    f.write("=" * 80 + "\n")
                    f.write("End of Report\n")

                messagebox.showinfo("Export Successful", f"Results exported to:\n{filename}")

            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export:\n{str(e)}")

    def _export_csv(self):
        """Export adjustment results to CSV file."""
        if not self.result:
            messagebox.showinfo("No Results", "Please run adjustment first.")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="enhanced_network_adjustment.csv"
        )

        if filename:
            try:
                import csv
                with open(filename, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)

                    # Header
                    writer.writerow(['Enhanced Network Adjustment Results'])
                    writer.writerow(['Method', self.method_var.get().capitalize()])
                    writer.writerow(['Sigma_0', f"{self.result.mse_unit_weight:.6f}"])
                    writer.writerow([])

                    # Adjusted Heights
                    writer.writerow(['ADJUSTED HEIGHTS'])
                    writer.writerow(['No.', 'Point', 'Height (m)', 'Std Error (m)', 'Status'])

                    for idx, (point, height) in enumerate(sorted(self.result.adjusted_heights.items()), 1):
                        mse = self.result.mse_heights.get(point, 0.0)
                        status = "Fixed" if point in self.fixed_points else "Adjusted"
                        writer.writerow([idx, point, f"{height:.4f}", f"{mse:.5f}", status])

                    writer.writerow([])

                    # Residuals
                    writer.writerow(['RESIDUALS'])
                    writer.writerow(['From', 'To', 'Observed dH (m)', 'Adjusted dH (m)', 'Residual (mm)'])

                    for line in self.lines:
                        from_pt = line.start_point
                        to_pt = line.end_point
                        observed_dh = line.height_diff

                        if from_pt in self.result.adjusted_heights and to_pt in self.result.adjusted_heights:
                            adjusted_dh = self.result.adjusted_heights[to_pt] - self.result.adjusted_heights[from_pt]
                            residual_mm = (observed_dh - adjusted_dh) * 1000

                            writer.writerow([from_pt, to_pt, f"{observed_dh:.4f}", f"{adjusted_dh:.4f}", f"{residual_mm:.2f}"])

                messagebox.showinfo("Export Successful", f"Results exported to:\n{filename}")

            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export:\n{str(e)}")

    def _export_plot(self):
        """Export visualization plot to image file."""
        if not self.result:
            messagebox.showinfo("No Results", "Please run adjustment first.")
            return

        if not MATPLOTLIB_AVAILABLE:
            messagebox.showerror("Matplotlib Not Available", "Matplotlib is required for plot export.")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[
                ("PNG files", "*.png"),
                ("PDF files", "*.pdf"),
                ("SVG files", "*.svg"),
                ("All files", "*.*")
            ],
            initialfile="residuals_plot.png"
        )

        if filename:
            try:
                # Create the same figure as in visualization
                fig = Figure(figsize=(12, 6), dpi=150)

                # Create two subplots
                ax1 = fig.add_subplot(121)
                ax2 = fig.add_subplot(122)

                # Collect residuals
                residuals_mm = []
                for line in self.lines:
                    from_pt = line.start_point
                    to_pt = line.end_point
                    observed_dh = line.height_diff

                    if from_pt in self.result.adjusted_heights and to_pt in self.result.adjusted_heights:
                        adjusted_dh = self.result.adjusted_heights[to_pt] - self.result.adjusted_heights[from_pt]
                        residual = (observed_dh - adjusted_dh) * 1000
                        residuals_mm.append(residual)

                # Plot 1: Bar chart
                colors = ['red' if abs(r) > 3.0 else 'orange' if abs(r) > 2.0 else 'green'
                          for r in residuals_mm]
                ax1.bar(range(len(residuals_mm)), residuals_mm, color=colors, alpha=0.7)
                ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
                ax1.axhline(y=2, color='orange', linestyle='--', linewidth=0.5, alpha=0.5)
                ax1.axhline(y=-2, color='orange', linestyle='--', linewidth=0.5, alpha=0.5)
                ax1.axhline(y=3, color='red', linestyle='--', linewidth=0.5, alpha=0.5)
                ax1.axhline(y=-3, color='red', linestyle='--', linewidth=0.5, alpha=0.5)
                ax1.set_xlabel('Observation Number')
                ax1.set_ylabel('Residual (mm)')
                ax1.set_title('Residuals by Observation')
                ax1.grid(True, alpha=0.3)

                # Plot 2: Histogram
                ax2.hist(residuals_mm, bins=20, color='steelblue', alpha=0.7, edgecolor='black')
                ax2.axvline(x=0, color='black', linestyle='-', linewidth=1)
                ax2.set_xlabel('Residual (mm)')
                ax2.set_ylabel('Frequency')
                ax2.set_title('Residual Distribution')
                ax2.grid(True, alpha=0.3)

                # Add statistics
                import numpy as np
                mean_res = np.mean(residuals_mm)
                std_res = np.std(residuals_mm)
                ax2.text(
                    0.95, 0.95,
                    f'Mean: {mean_res:.2f} mm\nStd: {std_res:.2f} mm\nσ₀: {self.result.mse_unit_weight:.4f}',
                    transform=ax2.transAxes,
                    verticalalignment='top',
                    horizontalalignment='right',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5)
                )

                fig.tight_layout()

                # Save figure
                fig.savefig(filename, dpi=150, bbox_inches='tight')

                messagebox.showinfo("Export Successful", f"Plot exported to:\n{filename}")

            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export plot:\n{str(e)}")


class GeodeticToolGUI:
    """Main GUI application for the Geodetic Leveling Tool."""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Geodetic Leveling Tool - פילוס גיאודטי")
        self.root.geometry("1200x800")
        self.root.minsize(800, 600)
        
        # Data storage
        self.lines: List[LevelingLine] = []
        self.file_paths: List[str] = []

        # NEW: Project management
        from ..config.models import ProjectData
        from ..config.project_manager import ProjectManager
        self.current_project: Optional[ProjectData] = ProjectData(name="Unnamed Project")
        self.project_manager = ProjectManager()

        # Create the GUI
        self._create_menu()
        self._create_main_layout()
        self._create_status_bar()

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
        file_menu.add_command(label="Exit / יציאה", command=self.root.quit, accelerator="Alt+F4")

        # NEW: Project menu
        project_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Project / פרויקט", menu=project_menu)
        project_menu.add_command(label="Save Project... / שמור פרויקט", command=self._save_project)
        project_menu.add_command(label="Load Project... / טען פרויקט", command=self._load_project)
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
        analysis_menu.add_command(label="Line Adjustment / תיאום קו", command=self._line_adjustment)
        analysis_menu.add_command(label="Network Adjustment (LSA) / תיאום רשת", command=self._network_adjustment)
        analysis_menu.add_command(label="Network Adjustment (Enhanced) / תיאום רשת משופר",
                                 command=self._network_adjustment_enhanced, accelerator="Ctrl+Shift+N")
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help / עזרה", menu=help_menu)
        help_menu.add_command(label="Documentation / תיעוד", command=self._show_docs)
        help_menu.add_command(label="About / אודות", command=self._show_about)
        
        # Keyboard shortcuts
        self.root.bind('<Control-o>', lambda e: self._open_files())
        self.root.bind('<Control-Shift-N>', lambda e: self._network_adjustment_enhanced())
    
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
        for i, (label_text, var_name) in enumerate(labels):
            ttk.Label(details, text=label_text).grid(row=i, column=0, sticky=tk.W, padx=5, pady=2)
            var = tk.StringVar(value="-")
            self.detail_vars[var_name] = var
            ttk.Label(details, textvariable=var).grid(row=i, column=1, sticky=tk.W, padx=5, pady=2)
        
        # Quick action button
        ttk.Button(details, text="Adjust This Line / תאם קו זה", 
                   command=self._adjust_selected_line).grid(row=len(labels), column=0, columnspan=2, pady=10)
        
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
        """Create the validation results panel."""
        columns = ('File', 'Start', 'End', 'Setups', 'Distance', 'dH', 'Status', 'Errors')
        self.validation_tree = ttk.Treeview(parent, columns=columns, show='headings')
        
        for col in columns:
            self.validation_tree.heading(col, text=col)
        
        self.validation_tree.column('File', width=100)
        self.validation_tree.column('Start', width=80)
        self.validation_tree.column('End', width=80)
        self.validation_tree.column('Setups', width=60)
        self.validation_tree.column('Distance', width=80)
        self.validation_tree.column('dH', width=100)
        self.validation_tree.column('Status', width=80)
        self.validation_tree.column('Errors', width=200)
        
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.validation_tree.yview)
        self.validation_tree.configure(yscrollcommand=scrollbar.set)
        
        self.validation_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
    
    def _create_analysis_panel(self, parent: ttk.Frame):
        """Create the analysis results panel."""
        self.analysis_text = scrolledtext.ScrolledText(parent, font=('Consolas', 10))
        self.analysis_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_log_panel(self, parent: ttk.Frame):
        """Create the log panel."""
        self.log_text = scrolledtext.ScrolledText(parent, font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_status_bar(self):
        """Create the status bar."""
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def _set_status(self, message: str):
        """Update status bar."""
        self.status_var.set(message)
        self.root.update_idletasks()
    
    def _log(self, message: str):
        """Add message to log."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
    
    def _open_files(self):
        """Open file dialog to select files."""
        filetypes = [
            ("All supported", "*.DAT *.dat *.RAW *.raw *.GSI *.gsi"),
            ("Trimble DAT", "*.DAT *.dat"),
            ("Leica RAW", "*.RAW *.raw"),
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
            folder_path = Path(folder)
            files = list(folder_path.glob("*.DAT")) + list(folder_path.glob("*.dat"))
            files += list(folder_path.glob("*.RAW")) + list(folder_path.glob("*.raw"))
            
            if files:
                self._load_files([str(f) for f in files])
            else:
                messagebox.showinfo("No Files", "No supported files found in folder")
    
    def _load_files(self, file_paths: List[str]):
        """Load and parse files."""
        self._set_status("Loading files...")
        
        for file_path in file_paths:
            if file_path in self.file_paths:
                continue
            
            try:
                parser = create_parser(file_path)
                line = parser.parse(file_path)
                
                self.lines.append(line)
                self.file_paths.append(file_path)
                
                # Add to listbox with used marker
                display_name = Path(file_path).name
                used_marker = "✓" if line.is_used else "✗"
                self.file_listbox.insert(tk.END, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")

                self._log(f"Loaded: {display_name}")
                
            except Exception as e:
                self._log(f"Error loading {Path(file_path).name}: {str(e)}")
        
        # Update summary
        total_dist = sum(line.total_distance for line in self.lines)
        self.summary_label.config(text=f"{len(self.lines)} files, {total_dist:.0f} m total")
        self._set_status("Ready")
    
    def _clear_files(self):
        """Clear all loaded files."""
        self.lines.clear()
        self.file_paths.clear()
        self.file_listbox.delete(0, tk.END)
        self._clear_details()
        self.summary_label.config(text="No files loaded")
        self._log("Cleared all files")
    
    def _reload_files(self):
        """Reload all files."""
        paths = self.file_paths.copy()
        self._clear_files()
        self._load_files(paths)
    
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
    
    def _show_line_details(self, line: LevelingLine):
        """Display details for selected line."""
        self.detail_vars['filename'].set(line.filename or "-")
        self.detail_vars['start_point'].set(line.start_point or "-")
        self.detail_vars['end_point'].set(line.end_point or "-")
        self.detail_vars['method'].set(line.method.value if hasattr(line.method, 'value') else str(line.method) if line.method else "-")
        self.detail_vars['setups'].set(str(len(line.setups)))
        self.detail_vars['distance'].set(f"{line.total_distance:.2f} m")
        self.detail_vars['height_diff'].set(f"{line.total_height_diff:.5f} m")
        self.detail_vars['status'].set(line.status.value if hasattr(line.status, 'value') else str(line.status))
        
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
        """Validate all loaded files."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        
        self._set_status("Validating...")
        
        # Clear previous results
        self.validation_tree.delete(*self.validation_tree.get_children())
        
        validator = BatchValidator()
        results = validator.validate_batch(self.lines)
        
        for line, result in results:
            status_text = "✓ Valid" if result.is_valid else "✗ Invalid"
            errors = "; ".join(result.errors) if result.errors else ""
            
            self.validation_tree.insert('', tk.END, values=(
                line.filename or "-",
                line.start_point or "-",
                line.end_point or "-",
                len(line.setups),
                f"{line.total_distance:.2f}",
                f"{line.total_height_diff:.5f}",
                status_text,
                errors
            ))
        
        # Switch to validation tab
        self.notebook.select(1)
        self._set_status("Validation complete")
        self._log(f"Validated {len(self.lines)} files")
    
    def _detect_double_runs(self):
        """Detect double-run pairs."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        
        pairs = detect_double_runs(self.lines)
        analyzer = LoopAnalyzer()
        
        self.analysis_text.delete('1.0', tk.END)
        self.analysis_text.insert(tk.END, "=== DOUBLE-RUN ANALYSIS / ניתוח הלוך-שוב ===\n\n")
        
        if not pairs:
            self.analysis_text.insert(tk.END, "No double-run pairs detected.\n")
        else:
            for fwd, ret in pairs:
                result = analyzer.analyze_double_run(fwd, ret)
                
                self.analysis_text.insert(tk.END, f"Pair: {fwd.start_point} ↔ {fwd.end_point}\n")
                self.analysis_text.insert(tk.END, f"  Forward file:  {fwd.filename}\n")
                self.analysis_text.insert(tk.END, f"  Return file:   {ret.filename}\n")
                
                if result['valid']:
                    self.analysis_text.insert(tk.END, f"  Forward dH:    {result['forward_dh']*1000:.3f} mm\n")
                    self.analysis_text.insert(tk.END, f"  Return dH:     {result['return_dh']*1000:.3f} mm\n")
                    self.analysis_text.insert(tk.END, f"  Misclosure:    {result['misclosure_mm']:.3f} mm\n")
                    self.analysis_text.insert(tk.END, f"  Mean dH:       {result['mean_dh']*1000:.3f} mm\n")
                    self.analysis_text.insert(tk.END, f"  Total dist:    {result['total_distance']:.2f} m\n")
                    self.analysis_text.insert(tk.END, f"  Class:         {result['tolerance_class'] or 'Exceeds all'}\n")
                    
                    if result['within_tolerance']:
                        self.analysis_text.insert(tk.END, f"  Status:        ✓ PASS\n")
                    else:
                        self.analysis_text.insert(tk.END, f"  Status:        ✗ FAIL (exceeds {result['tolerance_mm']:.2f} mm)\n")
                
                self.analysis_text.insert(tk.END, "\n")
        
        self.notebook.select(2)
        self._log(f"Found {len(pairs)} double-run pairs")
    
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
    
    def _adjust_selected_line(self):
        """Adjust the currently selected line."""
        selection = self.file_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select a line first")
            return
        
        index = selection[0]
        if index < len(self.lines):
            dialog = LineAdjustmentDialog(self.root, self.lines[index])
            self.root.wait_window(dialog)
    
    def _line_adjustment(self):
        """Open line adjustment dialog for selected line."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        
        selection = self.file_listbox.curselection()
        if not selection:
            # Ask user to select a line
            messagebox.showinfo("Select Line", "Please select a line from the list first")
            return
        
        index = selection[0]
        if index < len(self.lines):
            dialog = LineAdjustmentDialog(self.root, self.lines[index])
            self.root.wait_window(dialog)
            if dialog.result:
                self._log(f"Line adjustment completed for {self.lines[index].filename}")
    
    def _network_adjustment(self):
        """Open network adjustment dialog."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return

        if len(self.lines) < 2:
            messagebox.showinfo("Not Enough Lines", "Network adjustment requires at least 2 lines")
            return

        dialog = NetworkAdjustmentDialog(self.root, self.lines)
        self.root.wait_window(dialog)
        if dialog.result:
            self._log("Network adjustment completed")

    def _network_adjustment_enhanced(self):
        """Open enhanced network adjustment dialog."""
        if not self.lines:
            messagebox.showinfo(
                "No Data",
                "Please load leveling files first."
            )
            return

        if len(self.lines) < 2:
            messagebox.showinfo(
                "Not Enough Lines",
                "Network adjustment requires at least 2 lines."
            )
            return

        dialog = EnhancedNetworkAdjustmentDialog(self.root, self.lines)
        self.root.wait_window(dialog)
        if dialog.result:
            self._log("Enhanced network adjustment completed")
    
    def _export_results(self):
        """Export results to files."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        
        folder = filedialog.askdirectory(title="Select Output Folder / בחר תיקיית יעד")
        if folder:
            try:
                from ..exporters import FA0Exporter, FTEGExporter
                from ..gis.geojson_export import GeoJSONExporter
                
                # Export FTEG (only used lines)
                fteg_path = Path(folder) / "lines.FTEG"
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
                geojson_path = Path(folder) / "lines.geojson"
                gj = GeoJSONExporter()
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
• Trimble DAT (pipe-delimited)
• Leica RAW/GSI (fixed-width)

WORKFLOW:
1. Load Files: File > Open Files or Open Folder
2. Validate: Analysis > Validate All
3. Review: Check Line Details tab
4. Adjust: Analysis > Line Adjustment (single line)
         or Analysis > Network Adjustment (LSA)
5. Export: File > Export Results

LINE ADJUSTMENT (תיאום קו):
- Select a line from the list
- Enter known start and end heights
- Choose leveling class (1-4)
- View misclosure and intermediate heights

NETWORK ADJUSTMENT (תיאום רשת):
- Requires 2+ lines
- Select fixed points (benchmarks)
- Enter their known heights
- Run LSA to get adjusted heights

TOLERANCE CLASSES:
• Class 1: 3 mm × √km (First order)
• Class 2: 5 mm × √km (Second order)
• Class 3: 10 mm × √km (Third order)
• Class 4: 20 mm × √km (Fourth order)
        """
        messagebox.showinfo("Documentation", docs_text)
    
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

        # Sync lines to project
        self.current_project.lines = self.lines

        # Ask for project name if unnamed
        if self.current_project.name == "Unnamed Project":
            name = simpledialog.askstring("Project Name", "Enter project name:")
            if name:
                self.current_project.name = name

        # Ask for save location
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Project", "*.json"), ("Pickle Project", "*.pickle"), ("All files", "*.*")],
            initialfile=f"{self.current_project.name}.json"
        )

        if filepath:
            try:
                format = "json" if filepath.endswith(".json") else "pickle"
                self.current_project.project_path = filepath
                saved_path = self.project_manager.save_project(self.current_project, format=format)
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

                # Update UI
                self.file_listbox.delete(0, tk.END)
                for line in self.lines:
                    display_name = line.filename or f"{line.start_point}-{line.end_point}"
                    used_marker = "✓" if line.is_used else "✗"
                    self.file_listbox.insert(tk.END, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")

                total_dist = sum(line.total_distance for line in self.lines)
                self.summary_label.config(text=f"{len(self.lines)} files, {total_dist:.0f} m total")
                self.root.title(f"Geodetic Leveling Tool - {project.name}")

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

        folder = filedialog.askdirectory(title="Select Output Folder for QGIS Export")
        if folder:
            try:
                from ..gis.qgis_integration import QGISVirtualLayerBuilder

                # Sync lines to project
                self.current_project.lines = self.lines

                builder = QGISVirtualLayerBuilder()
                output_files = builder.export_for_qgis(self.current_project, folder, include_geojson=True)

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
            line.toggle_direction()

            # Update display
            self._show_line_details(line)
            messagebox.showinfo("Direction Toggled",
                f"Line direction changed:\n"
                f"{old_method} → {line.method}\n\n"
                f"Start: {line.start_point}\n"
                f"End: {line.end_point}\n"
                f"Height difference inverted: {line.total_height_diff:.5f} m"
            )
            self._log(f"Toggled direction for {line.filename}: {old_method} → {line.method}")

    def _toggle_line_used(self):
        """Toggle is_used flag for selected line."""
        selection = self.file_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select a line first")
            return

        index = selection[0]
        if index < len(self.lines):
            line = self.lines[index]
            line.is_used = not line.is_used

            # Update display
            display_name = line.filename or f"{line.start_point}-{line.end_point}"
            used_marker = "✓" if line.is_used else "✗"
            self.file_listbox.delete(index)
            self.file_listbox.insert(index, f"{used_marker} {display_name}: {line.start_point} → {line.end_point}")
            self.file_listbox.selection_set(index)

            status = "Included" if line.is_used else "Excluded"
            self._log(f"{status}: {line.filename}")


def main():
    """Main entry point for the GUI application."""
    root = tk.Tk()
    app = GeodeticToolGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
