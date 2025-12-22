"""
Exporters Package

Export modules for generating geodetic output files.
All exports use ANSI encoding (cp1255 for Hebrew).
"""
from typing import List, Dict, Optional, TextIO
from pathlib import Path
from datetime import datetime
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.models import (
    LevelingLine, MeasurementSummary, Benchmark, AdjustmentResult
)
from config.settings import get_settings


logger = logging.getLogger(__name__)


class FA0Exporter:
    """
    Export FA0 format files.
    
    FA0 Format (adjustment input):
        Line 1: Header (num_points, type, filename)
        Lines 2-N: Benchmark heights (point_id, height)
        Following: Observation data (from, to, dh, dist, setups, bf_diff, date, source)
        Last line: Terminator (9)
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding
    
    def export(
        self,
        filepath: str,
        benchmarks: List[Benchmark],
        observations: List[MeasurementSummary],
        project_name: str = "project.rez"
    ):
        """
        Export to FA0 format.
        
        Args:
            filepath: Output file path
            benchmarks: List of known benchmarks
            observations: List of measurement summaries
            project_name: Project/output filename reference
        """
        with open(filepath, 'w', encoding=self.encoding) as f:
            # Header line
            num_points = len(benchmarks) + self._count_unknown_points(observations, benchmarks)
            f.write(f"  {num_points}   2            {project_name:40}9\n")
            
            # Benchmark heights
            for bm in benchmarks:
                # Fixed points have actual heights
                f.write(f"    {bm.point_id:8}{bm.height:12.3f}\n")
            
            # Unknown points (mark with .)
            unknown_points = self._get_unknown_points(observations, benchmarks)
            for point_id in unknown_points:
                f.write(f"    {point_id:8}           .\n")
            
            # Observation data
            for obs in observations:
                # Format: from_pt  to_pt  dh  dist  setups  bf_diff  date  source
                line = (
                    f"{obs.from_point:8}{obs.to_point:8}"
                    f"{obs.height_diff:12.5f}"
                    f"{obs.distance:7.0f}."
                    f"{obs.num_setups:4}"
                    f"{obs.bf_diff:7.2f}"
                    f"  {obs.year_month:4}"
                    f"           {obs.source_file}\n"
                )
                f.write(line)
            
            # Terminator
            f.write(" 9\n")
    
    def _count_unknown_points(
        self,
        observations: List[MeasurementSummary],
        benchmarks: List[Benchmark]
    ) -> int:
        """Count points that are not in the benchmark list."""
        return len(self._get_unknown_points(observations, benchmarks))
    
    def _get_unknown_points(
        self,
        observations: List[MeasurementSummary],
        benchmarks: List[Benchmark]
    ) -> List[str]:
        """Get list of unknown points."""
        known = {bm.point_id for bm in benchmarks}
        all_points = set()
        for obs in observations:
            all_points.add(obs.from_point)
            all_points.add(obs.to_point)
        return sorted(all_points - known)


class FA1Exporter:
    """
    Export FA1 format files.
    
    FA1 Format (adjustment output/report):
        Contains detailed adjustment iterations with:
        - Input data summary
        - Observation residuals
        - Adjusted heights with M.S.E.
        - Statistical summaries
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding
    
    def export(
        self,
        filepath: str,
        benchmarks: List[Benchmark],
        observations: List[MeasurementSummary],
        result: AdjustmentResult,
        project_name: str = "project.rez"
    ):
        """
        Export to FA1 format.
        
        Args:
            filepath: Output file path
            benchmarks: List of known benchmarks
            observations: List of observations
            result: AdjustmentResult from adjustment
            project_name: Project name
        """
        with open(filepath, 'w', encoding=self.encoding) as f:
            # Data section header
            self._write_data_section(f, benchmarks, observations, project_name)
            
            # Observation residuals
            self._write_residuals(f, observations, result)
            
            # Summary statistics
            self._write_statistics(f, result)
            
            # Adjusted heights
            self._write_adjusted_heights(f, result, benchmarks)
    
    def _write_data_section(
        self,
        f: TextIO,
        benchmarks: List[Benchmark],
        observations: List[MeasurementSummary],
        project_name: str
    ):
        """Write the DATA section."""
        f.write("\n\n")
        f.write(" " * 43 + "DATA\n")
        f.write(" " * 42 + "******\n\n")
        
        num_points = len(set(
            [bm.point_id for bm in benchmarks] +
            [obs.from_point for obs in observations] +
            [obs.to_point for obs in observations]
        ))
        
        f.write(f"{' ' * 43}{num_points}   2            {project_name:40}9\n\n\n\n")
        
        # Point heights
        f.write(" " * 43 + "DATA\n")
        f.write(" " * 42 + "******\n")
        
        # Fixed benchmarks
        idx = 1
        known_heights = {bm.point_id: bm.height for bm in benchmarks}
        all_points = set(bm.point_id for bm in benchmarks)
        for obs in observations:
            all_points.add(obs.from_point)
            all_points.add(obs.to_point)
        
        for point_id in sorted(all_points):
            if point_id in known_heights:
                height = known_heights[point_id]
                f.write(f"{' ' * 42}{idx:3}  {point_id:8}{height:12.3f}\n")
            else:
                f.write(f"{' ' * 42}{idx:3}  {point_id:8}{0.0:12.3f}\n")
            idx += 1
        
        f.write("0\n\n1\n\n\n")
    
    def _write_residuals(
        self,
        f: TextIO,
        observations: List[MeasurementSummary],
        result: AdjustmentResult
    ):
        """Write observation residuals table."""
        header = (
            "         FROM   TO        DH        DISTANCE   DH-(H2-H1)     "
            "ALWD      DELTA    G/B   KOD  YEARS  DURATION   NO.SH\n"
        )
        f.write(header)
        f.write("        " + "-" * 107 + "\n\n")
        
        for obs in observations:
            key = f"{obs.from_point}-{obs.to_point}"
            residual = result.residuals.get(key, 0) / 1000  # Convert back to m
            
            # Calculate allowable tolerance
            dist_km = obs.distance / 1000
            tolerance = 0.003 * (dist_km ** 0.5) if dist_km > 0 else 0
            
            delta = residual - tolerance
            
            # Determine if good/bad
            status = "  " if abs(residual) <= tolerance else "**"
            
            line = (
                f"      {obs.from_point:8}{obs.to_point:8}"
                f"{obs.height_diff:12.5f}"
                f"{obs.distance:8.0f}."
                f"{residual:12.5f}"
                f"    {tolerance:.6f}"
                f"{delta:12.6f}  {status}    0    0- 0      0\n\n"
            )
            f.write(line)
    
    def _write_statistics(self, f: TextIO, result: AdjustmentResult):
        """Write summary statistics."""
        f.write(",                 " + "=" * 75 + "\n")
        f.write(
            f"                   SDIST(KM)={result.total_distance_km:9.3f}   "
            f"SDIF(MM)={result.total_diff_mm:12.1f}   "
            f"COEFF_CLASS K={result.k_coefficient:10.1f}\n\n"
        )
    
    def _write_adjusted_heights(
        self,
        f: TextIO,
        result: AdjustmentResult,
        benchmarks: List[Benchmark]
    ):
        """Write adjusted heights table."""
        f.write(f"1{' ' * 45}ITERATION  NO. {result.iteration}\n\n")
        f.write(f"{' ' * 39}project.rez\n\n")
        f.write(f"{' ' * 37}M.S.E. OF UNIT WEIGHT = {result.mse_unit_weight:10.6f}\n\n")
        f.write(f"{' ' * 34}NO.      ADJUSTED     APPROX          DIF         M.S.E.\n\n")
        
        fixed_points = {bm.point_id for bm in benchmarks}
        
        idx = 1
        for point_id in sorted(result.adjusted_heights.keys()):
            height = result.adjusted_heights[point_id]
            mse = result.mse_heights.get(point_id, 0)
            
            if point_id in fixed_points:
                # Fixed point - no MSE
                f.write(f"{' ' * 30}{idx:3} {point_id:8}{height:12.5f}\n")
            else:
                # Adjusted point
                approx = 0.0  # Would need to track approximate
                diff = height - approx
                f.write(
                    f"{' ' * 30}{idx:3} {point_id:8}{height:12.5f}"
                    f"{approx:12.5f}{diff:12.5f}{mse:12.6f}\n"
                )
            idx += 1


class FTEGExporter:
    """
    Export FTEG format files.
    
    FTEG Format (simplified measurement data):
        from_pt  to_pt  dh  dist  setups  bf_diff  date  source  terminator
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding
    
    def export(
        self,
        filepath: str,
        observations: List[MeasurementSummary]
    ):
        """
        Export to FTEG format.
        
        Args:
            filepath: Output file path
            observations: List of measurement summaries
        """
        with open(filepath, 'w', encoding=self.encoding) as f:
            for i, obs in enumerate(observations):
                # Last observation gets terminator 9, others get 0
                terminator = "9" if i == len(observations) - 1 else "0"
                
                line = (
                    f"{obs.from_point:8}{obs.to_point:8}"
                    f"{obs.height_diff:12.5f}"
                    f"{obs.distance:7.0f}."
                    f"{obs.num_setups:4}"
                    f"{obs.bf_diff:7.2f}"
                    f"   {obs.year_month:3}"
                    f"           {obs.source_file} {terminator}\n"
                )
                f.write(line)


class REZExporter:
    """
    Export REZ format files (summary/results).
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding
    
    def export(
        self,
        filepath: str,
        lines: List[LevelingLine],
        project_name: str = "project"
    ):
        """
        Export summary REZ file.
        
        Args:
            filepath: Output file path
            lines: List of LevelingLine objects
            project_name: Project identifier
        """
        with open(filepath, 'w', encoding=self.encoding) as f:
            f.write(f"# REZ Summary File - {project_name}\n")
            f.write(f"# Generated: {datetime.now().isoformat()}\n")
            f.write("#" + "=" * 78 + "\n\n")
            
            f.write(f"{'From':<12}{'To':<12}{'Height Diff':>14}{'Distance':>12}{'Setups':>8}{'Status':>12}\n")
            f.write("-" * 70 + "\n")
            
            for line in lines:
                status_str = "OK" if line.status.value == "valid" else line.status.value
                f.write(
                    f"{line.start_point:<12}{line.end_point:<12}"
                    f"{line.total_height_diff:>14.5f}{line.total_distance:>12.2f}"
                    f"{line.num_setups:>8}{status_str:>12}\n"
                )


# Convenience functions
def export_fa0(
    filepath: str,
    benchmarks: List[Benchmark],
    observations: List[MeasurementSummary],
    project_name: str = "project.rez"
):
    """Export to FA0 format."""
    exporter = FA0Exporter()
    exporter.export(filepath, benchmarks, observations, project_name)


def export_fa1(
    filepath: str,
    benchmarks: List[Benchmark],
    observations: List[MeasurementSummary],
    result: AdjustmentResult,
    project_name: str = "project.rez"
):
    """Export to FA1 format."""
    exporter = FA1Exporter()
    exporter.export(filepath, benchmarks, observations, result, project_name)


def export_fteg(filepath: str, observations: List[MeasurementSummary]):
    """Export to FTEG format."""
    exporter = FTEGExporter()
    exporter.export(filepath, observations)


def export_rez(
    filepath: str,
    lines: List[LevelingLine],
    project_name: str = "project"
):
    """Export to REZ format."""
    exporter = REZExporter()
    exporter.export(filepath, lines, project_name)
