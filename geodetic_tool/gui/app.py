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
from ..engine.least_squares import LeastSquaresAdjuster
from ..config.models import LevelingLine, Benchmark, AdjustmentResult
from ..config.settings import FileFormat, calculate_tolerance, is_benchmark


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
        
        # Right panel - Results
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=2)
        
        results_frame = ttk.LabelFrame(right_frame, text="Adjustment Results / תוצאות תיאום")
        results_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.results_text = scrolledtext.ScrolledText(results_frame, font=('Consolas', 10))
        self.results_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
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
        file_menu.add_separator()
        file_menu.add_command(label="Exit / יציאה", command=self.root.quit, accelerator="Alt+F4")
        
        # Analysis menu
        analysis_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Analysis / ניתוח", menu=analysis_menu)
        analysis_menu.add_command(label="Validate All / בדיקת תקינות", command=self._validate_all)
        analysis_menu.add_command(label="Detect Double-Runs / זיהוי הלוך-שוב", command=self._detect_double_runs)
        analysis_menu.add_command(label="Find Loops / חיפוש לולאות", command=self._find_loops)
        analysis_menu.add_separator()
        analysis_menu.add_command(label="Line Adjustment / תיאום קו", command=self._line_adjustment)
        analysis_menu.add_command(label="Network Adjustment (LSA) / תיאום רשת", command=self._network_adjustment)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help / עזרה", menu=help_menu)
        help_menu.add_command(label="Documentation / תיעוד", command=self._show_docs)
        help_menu.add_command(label="About / אודות", command=self._show_about)
        
        # Keyboard shortcuts
        self.root.bind('<Control-o>', lambda e: self._open_files())
    
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
                
                # Add to listbox
                display_name = Path(file_path).name
                self.file_listbox.insert(tk.END, f"{display_name}: {line.start_point} → {line.end_point}")
                
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
    
    def _export_results(self):
        """Export results to files."""
        if not self.lines:
            messagebox.showinfo("No Files", "Please load files first")
            return
        
        folder = filedialog.askdirectory(title="Select Output Folder / בחר תיקיית יעד")
        if folder:
            try:
                from exporters import FA0Exporter, FTEGExporter
                from gis.geojson_export import GeoJSONExporter
                
                # Export FTEG
                fteg_path = Path(folder) / "lines.FTEG"
                fteg = FTEGExporter()
                fteg.export(self.lines, str(fteg_path))
                
                # Export GeoJSON
                geojson_path = Path(folder) / "lines.geojson"
                gj = GeoJSONExporter()
                gj.export(self.lines, str(geojson_path))
                
                messagebox.showinfo("Export", f"Files exported to:\n{folder}")
                self._log(f"Exported to {folder}")
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


def main():
    """Main entry point for the GUI application."""
    root = tk.Tk()
    app = GeodeticToolGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
