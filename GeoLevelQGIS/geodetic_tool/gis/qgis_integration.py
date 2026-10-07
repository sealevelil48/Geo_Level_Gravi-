"""
QGIS Integration Module

Provides functionality to load geodetic leveling data directly into QGIS
as virtual layers with CRS 2039 (Israel TM Grid).
"""
from typing import List, Optional, Dict
from pathlib import Path
import json

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.models import LevelingLine, ProjectData


class QGISVirtualLayerBuilder:
    """
    Build QGIS Virtual Layers from geodetic leveling data.

    Creates point and line layers with CRS 2039 (Israel TM Grid).
    """

    def __init__(self, crs: str = "EPSG:2039"):
        """
        Initialize QGIS layer builder.

        Args:
            crs: Coordinate Reference System (default: EPSG:2039 - Israel TM Grid)
        """
        self.crs = crs

    def create_points_layer_uri(self,
                                 lines: List[LevelingLine],
                                 layer_name: str = "Leveling Points") -> str:
        """
        Create a QGIS Virtual Layer URI for points.

        Args:
            lines: List of LevelingLine objects
            layer_name: Name for the layer

        Returns:
            Virtual layer URI string for QGIS
        """
        # Collect all unique points
        points = {}  # point_id -> properties

        for line in lines:
            if not line.is_used:
                continue

            # Start point
            if line.start_point not in points:
                points[line.start_point] = {
                    'point_id': line.start_point,
                    'is_benchmark': not line.start_point.isdigit(),
                    'type': 'endpoint'
                }

            # End point
            if line.end_point not in points:
                points[line.end_point] = {
                    'point_id': line.end_point,
                    'is_benchmark': not line.end_point.isdigit(),
                    'type': 'endpoint'
                }

        # Build WKT-based virtual layer
        # Note: Without actual coordinates, we create a simple attribute table
        # Users should join with their coordinate database
        uri = f"?query=SELECT "
        uri += "'point' as type, "
        uri += "point_id, "
        uri += "is_benchmark, "
        uri += "point_type "
        uri += "FROM ("

        values = []
        for i, (point_id, props) in enumerate(points.items()):
            bm = 1 if props['is_benchmark'] else 0
            values.append(f"SELECT {i} as id, '{point_id}' as point_id, {bm} as is_benchmark, '{props['type']}' as point_type")

        uri += " UNION ALL ".join(values)
        uri += ")&geometry=none"
        uri += f"&uid=id&nogeometry"

        return uri

    def create_lines_layer_uri(self,
                                lines: List[LevelingLine],
                                layer_name: str = "Leveling Lines") -> str:
        """
        Create a QGIS Virtual Layer URI for leveling lines.

        Args:
            lines: List of LevelingLine objects
            layer_name: Name for the layer

        Returns:
            Virtual layer URI string for QGIS
        """
        # Build virtual layer with line attributes
        uri = "?query=SELECT "
        uri += "id, "
        uri += "filename, "
        uri += "start_point, "
        uri += "end_point, "
        uri += "method, "
        uri += "distance_m, "
        uri += "height_diff_m, "
        uri += "num_setups, "
        uri += "is_used "
        uri += "FROM ("

        values = []
        for i, line in enumerate(lines):
            if not line.is_used:
                continue

            filename = line.filename.replace("'", "''") if line.filename else ''
            values.append(
                f"SELECT {i} as id, "
                f"'{filename}' as filename, "
                f"'{line.start_point}' as start_point, "
                f"'{line.end_point}' as end_point, "
                f"'{line.method}' as method, "
                f"{line.total_distance} as distance_m, "
                f"{line.total_height_diff} as height_diff_m, "
                f"{line.num_setups} as num_setups, "
                f"{1 if line.is_used else 0} as is_used"
            )

        if not values:
            # Empty layer
            uri += "SELECT 0 as id, '' as filename, '' as start_point, '' as end_point, '' as method, 0.0 as distance_m, 0.0 as height_diff_m, 0 as num_setups, 0 as is_used WHERE 1=0"
        else:
            uri += " UNION ALL ".join(values)

        uri += ")&geometry=none&uid=id"

        return uri

    def generate_pyqgis_script(self,
                               project: ProjectData,
                               output_path: Optional[str] = None) -> str:
        """
        Generate a PyQGIS script to load the project data.

        This script can be run in the QGIS Python console.

        Args:
            project: ProjectData to export
            output_path: Optional path to save the script

        Returns:
            PyQGIS script as string
        """
        points_uri_str = self.create_points_layer_uri(project.lines, "Points")
        lines_uri_str = self.create_lines_layer_uri(project.lines, "Lines")

        script = f'''"""
PyQGIS Script: Load Geodetic Leveling Project
Project: {project.name}
Generated by Geodetic Leveling Tool
"""

from qgis.core import QgsVectorLayer, QgsProject, QgsCoordinateReferenceSystem
from qgis.utils import iface

# CRS: Israel TM Grid (EPSG:2039)
crs = QgsCoordinateReferenceSystem("EPSG:2039")

# Create Points Layer
points_uri = """{points_uri_str}"""

points_layer = QgsVectorLayer(points_uri, "{project.name} - Points", "virtual")
points_layer.setCrs(crs)

if points_layer.isValid():
    QgsProject.instance().addMapLayer(points_layer)
    print(f"Added points layer: {{{{points_layer.name()}}}}")
else:
    print("ERROR: Points layer is invalid")

# Create Lines Layer
lines_uri = """{lines_uri_str}"""

lines_layer = QgsVectorLayer(lines_uri, "{project.name} - Lines", "virtual")
lines_layer.setCrs(crs)

if lines_layer.isValid():
    QgsProject.instance().addMapLayer(lines_layer)
    print(f"Added lines layer: {{{{lines_layer.name()}}}}")
else:
    print("ERROR: Lines layer is invalid")

# Apply styling
from qgis.core import QgsSymbol, QgsSingleSymbolRenderer, QgsMarkerSymbol, QgsLineSymbol

# Style points layer
if points_layer.isValid():
    symbol = QgsMarkerSymbol.createSimple({{'color': 'blue', 'size': '3', 'name': 'triangle'}})
    renderer = QgsSingleSymbolRenderer(symbol)
    points_layer.setRenderer(renderer)
    points_layer.triggerRepaint()

# Style lines layer
if lines_layer.isValid():
    symbol = QgsLineSymbol.createSimple({{'color': 'red', 'width': '1.5'}})
    renderer = QgsSingleSymbolRenderer(symbol)
    lines_layer.setRenderer(renderer)
    lines_layer.triggerRepaint()

# Zoom to layers
if iface:
    iface.zoomToActiveLayer()

print("Leveling data loaded successfully!")
print(f"Total lines: {len(project.get_used_lines())}")
print(f"Total points: {len(project.get_all_points())}")
'''

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(script)

        return script

    def export_for_qgis(self,
                        project: ProjectData,
                        output_folder: str,
                        include_geojson: bool = True):
        """
        Export project data for QGIS loading.

        Creates:
        1. PyQGIS script for virtual layer
        2. GeoJSON files (if coordinates available)
        3. QML style files

        Args:
            project: ProjectData to export
            output_folder: Folder to save files
            include_geojson: Whether to include GeoJSON export
        """
        output_path = Path(output_folder)
        output_path.mkdir(parents=True, exist_ok=True)

        # Generate PyQGIS script
        script_path = output_path / f"{project.name}_load_in_qgis.py"
        self.generate_pyqgis_script(project, str(script_path))

        # Generate README
        readme_path = output_path / "README_QGIS.txt"
        self._generate_readme(project, readme_path)

        # Export GeoJSON if requested
        if include_geojson:
            try:
                from .geojson_export import export_network_to_geojson
                geojson_files = export_network_to_geojson(
                    project.get_used_lines(),
                    str(output_path),
                    project.name
                )
            except Exception as e:
                print(f"Warning: GeoJSON export failed: {e}")

        return {
            'pyqgis_script': str(script_path),
            'readme': str(readme_path)
        }

    def _generate_readme(self, project: ProjectData, readme_path: Path):
        """Generate README file with instructions."""
        content = f'''
QGIS Integration for Geodetic Leveling Project
================================================

Project: {project.name}
Type: {"Joint Project" if project.is_joint_project else "Single Project"}
Lines: {len(project.get_used_lines())} (used) / {len(project.lines)} (total)
Points: {len(project.get_all_points())}

HOW TO LOAD IN QGIS:
--------------------

Method 1: Using PyQGIS Script
1. Open QGIS
2. Open Python Console: Plugins → Python Console
3. Click "Show Editor" button
4. Open the file: {project.name}_load_in_qgis.py
5. Click "Run Script" button
6. The layers will be added with CRS EPSG:2039 (Israel TM Grid)

Method 2: Using GeoJSON Files
1. Open QGIS
2. Layer → Add Layer → Add Vector Layer
3. Select the .geojson files
4. Apply the .qml style files for proper visualization

CRS INFORMATION:
----------------
Default CRS: EPSG:2039 (Israel TM Grid)
All layers are configured with this CRS.

NOTES:
------
- Virtual layers do not contain geometry (coordinates)
- They show the attribute data and relationships
- To visualize on a map, you need to join with a coordinate database
- Alternatively, use the GeoJSON files if coordinates are available

LEGEND:
-------
Points:
  - Blue Triangles: Benchmarks (named points)
  - Orange Circles: Turning Points (numbered)

Lines:
  - Red Lines: Leveling runs
  - Thickness indicates number of setups
'''

        if project.is_joint_project:
            content += f"\nSOURCE PROJECTS:\n"
            for source in project.source_projects:
                content += f"  - {source}\n"

        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(content)


def load_project_in_qgis(project: ProjectData):
    """
    Convenience function to load a project directly in QGIS.

    This function should be called from within QGIS Python console.

    Args:
        project: ProjectData to load
    """
    try:
        from qgis.core import QgsVectorLayer, QgsProject

        builder = QGISVirtualLayerBuilder()

        # Load points
        points_uri = builder.create_points_layer_uri(project.lines)
        points_layer = QgsVectorLayer(points_uri, f"{project.name} - Points", "virtual")
        if points_layer.isValid():
            QgsProject.instance().addMapLayer(points_layer)

        # Load lines
        lines_uri = builder.create_lines_layer_uri(project.lines)
        lines_layer = QgsVectorLayer(lines_uri, f"{project.name} - Lines", "virtual")
        if lines_layer.isValid():
            QgsProject.instance().addMapLayer(lines_layer)

        print(f"Project '{project.name}' loaded successfully!")

    except ImportError:
        print("ERROR: This function must be run from within QGIS Python console")
