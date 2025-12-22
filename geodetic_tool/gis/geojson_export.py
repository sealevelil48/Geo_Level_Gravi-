"""
GIS Export Module

Export geodetic leveling data to GIS formats (GeoJSON, GeoPackage)
for visualization in QGIS and other GIS software.
"""
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.models import LevelingLine, Benchmark


@dataclass
class GeoPoint:
    """Geographic point with coordinates."""
    point_id: str
    latitude: float = 0.0
    longitude: float = 0.0
    height: Optional[float] = None
    properties: Dict = field(default_factory=dict)
    
    def to_geojson_feature(self) -> Dict:
        """Convert to GeoJSON Feature."""
        props = {
            'id': self.point_id,
            'height': self.height,
            **self.properties
        }
        
        return {
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [self.longitude, self.latitude, self.height or 0]
            },
            'properties': props
        }


@dataclass  
class GeoLine:
    """Geographic line connecting two points."""
    start_point: GeoPoint
    end_point: GeoPoint
    properties: Dict = field(default_factory=dict)
    
    def to_geojson_feature(self) -> Dict:
        """Convert to GeoJSON Feature."""
        return {
            'type': 'Feature',
            'geometry': {
                'type': 'LineString',
                'coordinates': [
                    [self.start_point.longitude, self.start_point.latitude, 
                     self.start_point.height or 0],
                    [self.end_point.longitude, self.end_point.latitude,
                     self.end_point.height or 0]
                ]
            },
            'properties': self.properties
        }


class CoordinateManager:
    """
    Manages coordinates for benchmarks and turning points.
    
    In a real application, this would load from a database or coordinate file.
    For now, it provides a structure for adding coordinates.
    """
    
    def __init__(self):
        self.coordinates: Dict[str, Tuple[float, float, float]] = {}
    
    def add_point(self, point_id: str, lon: float, lat: float, height: float = 0.0):
        """Add coordinates for a point."""
        self.coordinates[point_id] = (lon, lat, height)
    
    def get_coordinates(self, point_id: str) -> Optional[Tuple[float, float, float]]:
        """Get coordinates for a point."""
        return self.coordinates.get(point_id)
    
    def has_coordinates(self, point_id: str) -> bool:
        """Check if coordinates exist for a point."""
        return point_id in self.coordinates
    
    def load_from_file(self, filepath: str):
        """
        Load coordinates from a CSV or text file.
        
        Expected format: point_id,longitude,latitude,height
        """
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split(',')
                if len(parts) >= 3:
                    point_id = parts[0].strip()
                    lon = float(parts[1])
                    lat = float(parts[2])
                    height = float(parts[3]) if len(parts) > 3 else 0.0
                    self.add_point(point_id, lon, lat, height)
    
    def load_from_benchmarks(self, benchmarks: List[Benchmark]):
        """Load coordinates from benchmark objects (if they have coordinates)."""
        for bm in benchmarks:
            if hasattr(bm, 'longitude') and hasattr(bm, 'latitude'):
                self.add_point(bm.point_id, bm.longitude, bm.latitude, bm.height)


class GeoJSONExporter:
    """Export leveling data to GeoJSON format."""
    
    def __init__(self, coord_manager: Optional[CoordinateManager] = None):
        self.coord_manager = coord_manager or CoordinateManager()
        
    def export_lines(self, lines: List[LevelingLine], output_path: str,
                     include_schematic: bool = True) -> Dict:
        """
        Export leveling lines to GeoJSON.
        
        If coordinates are not available, creates a schematic layout.
        
        Args:
            lines: List of LevelingLine objects
            output_path: Path to output file
            include_schematic: If True, generate schematic coordinates for visualization
            
        Returns:
            GeoJSON FeatureCollection dict
        """
        features = []
        point_features = []
        all_points = set()
        
        # Collect all unique points
        for line in lines:
            if line.start_point:
                all_points.add(line.start_point)
            if line.end_point:
                all_points.add(line.end_point)
        
        # Generate schematic coordinates if needed
        if include_schematic:
            self._generate_schematic_coords(all_points, lines)
        
        # Create line features
        for line in lines:
            if not line.start_point or not line.end_point:
                continue
            
            start_coords = self.coord_manager.get_coordinates(line.start_point)
            end_coords = self.coord_manager.get_coordinates(line.end_point)
            
            if start_coords and end_coords:
                feature = {
                    'type': 'Feature',
                    'geometry': {
                        'type': 'LineString',
                        'coordinates': [
                            [start_coords[0], start_coords[1], start_coords[2]],
                            [end_coords[0], end_coords[1], end_coords[2]]
                        ]
                    },
                    'properties': {
                        'filename': line.filename,
                        'start_point': line.start_point,
                        'end_point': line.end_point,
                        'distance_m': line.total_distance,
                        'height_diff_m': line.total_height_diff,
                        'num_setups': len(line.setups),
                        'status': line.status.value if hasattr(line.status, 'value') else str(line.status),
                        'method': line.method.value if hasattr(line.method, 'value') else str(line.method) if line.method else None
                    }
                }
                features.append(feature)
        
        # Create point features
        for point_id in all_points:
            coords = self.coord_manager.get_coordinates(point_id)
            if coords:
                point_feature = {
                    'type': 'Feature',
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [coords[0], coords[1], coords[2]]
                    },
                    'properties': {
                        'point_id': point_id,
                        'height': coords[2],
                        'is_benchmark': not point_id.isdigit()
                    }
                }
                point_features.append(point_feature)
        
        # Combine into FeatureCollection
        geojson = {
            'type': 'FeatureCollection',
            'name': 'Leveling Network',
            'crs': {
                'type': 'name',
                'properties': {
                    'name': 'urn:ogc:def:crs:EPSG::4326'  # WGS84
                }
            },
            'features': features + point_features,
            'metadata': {
                'created': datetime.now().isoformat(),
                'num_lines': len(lines),
                'num_points': len(all_points),
                'generator': 'Geodetic Leveling Tool'
            }
        }
        
        # Write to file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
        
        return geojson
    
    def _generate_schematic_coords(self, points: set, lines: List[LevelingLine]):
        """
        Generate schematic coordinates for visualization when real coords are unavailable.
        
        Uses a force-directed layout approach for better visualization.
        """
        if not points:
            return
        
        import math
        
        # Start with a simple circular layout
        point_list = list(points)
        n = len(point_list)
        
        # Base coordinates (centered around 0,0 for schematic)
        base_lon = 34.8  # Approximate Israel longitude
        base_lat = 31.5  # Approximate Israel latitude
        radius = 0.01  # ~1km in degrees
        
        for i, point_id in enumerate(point_list):
            if not self.coord_manager.has_coordinates(point_id):
                angle = 2 * math.pi * i / n
                lon = base_lon + radius * math.cos(angle)
                lat = base_lat + radius * math.sin(angle)
                self.coord_manager.add_point(point_id, lon, lat, 0.0)
    
    def export_points_only(self, benchmarks: List[Benchmark], output_path: str) -> Dict:
        """Export just the benchmark points to GeoJSON."""
        features = []
        
        for bm in benchmarks:
            coords = self.coord_manager.get_coordinates(bm.point_id)
            if coords:
                feature = {
                    'type': 'Feature',
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [coords[0], coords[1], bm.height]
                    },
                    'properties': {
                        'point_id': bm.point_id,
                        'height': bm.height,
                        'order': bm.order
                    }
                }
                features.append(feature)
        
        geojson = {
            'type': 'FeatureCollection',
            'features': features
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, indent=2)
        
        return geojson


class QGISStyleGenerator:
    """Generate QGIS style files (QML) for the exported layers."""
    
    @staticmethod
    def generate_line_style(output_path: str, color: str = '#FF0000', width: float = 1.5):
        """Generate a QML style file for lines."""
        qml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<qgis version="3.0" styleCategories="AllStyleCategories">
  <renderer-v2 type="singleSymbol">
    <symbols>
      <symbol type="line" name="0">
        <layer class="SimpleLine" enabled="1">
          <prop k="line_color" v="{color}"/>
          <prop k="line_width" v="{width}"/>
          <prop k="line_style" v="solid"/>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings>
      <text-style fieldName="concat(start_point, ' → ', end_point)" fontSize="8"/>
    </settings>
  </labeling>
</qgis>'''
        
        with open(output_path, 'w') as f:
            f.write(qml_content)
    
    @staticmethod
    def generate_point_style(output_path: str):
        """Generate a QML style file for points."""
        qml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<qgis version="3.0" styleCategories="AllStyleCategories">
  <renderer-v2 type="categorizedSymbol" attr="is_benchmark">
    <categories>
      <category symbol="0" value="true" label="Benchmark"/>
      <category symbol="1" value="false" label="Turning Point"/>
    </categories>
    <symbols>
      <symbol type="marker" name="0">
        <layer class="SimpleMarker" enabled="1">
          <prop k="color" v="0,0,255,255"/>
          <prop k="size" v="4"/>
          <prop k="name" v="triangle"/>
        </layer>
      </symbol>
      <symbol type="marker" name="1">
        <layer class="SimpleMarker" enabled="1">
          <prop k="color" v="255,165,0,255"/>
          <prop k="size" v="3"/>
          <prop k="name" v="circle"/>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings>
      <text-style fieldName="point_id" fontSize="8"/>
    </settings>
  </labeling>
</qgis>'''
        
        with open(output_path, 'w') as f:
            f.write(qml_content)


def export_network_to_geojson(lines: List[LevelingLine], 
                               output_folder: str,
                               project_name: str = "leveling_network") -> Dict[str, str]:
    """
    Convenience function to export a complete network to GeoJSON files.
    
    Args:
        lines: List of LevelingLine objects
        output_folder: Output folder path
        project_name: Base name for output files
        
    Returns:
        Dictionary of output file paths
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)
    
    exporter = GeoJSONExporter()
    
    # Export lines and points
    lines_file = output_path / f"{project_name}_lines.geojson"
    exporter.export_lines(lines, str(lines_file))
    
    # Generate QGIS styles
    line_style = output_path / f"{project_name}_lines.qml"
    point_style = output_path / f"{project_name}_points.qml"
    
    QGISStyleGenerator.generate_line_style(str(line_style))
    QGISStyleGenerator.generate_point_style(str(point_style))
    
    return {
        'lines_geojson': str(lines_file),
        'line_style': str(line_style),
        'point_style': str(point_style)
    }


if __name__ == '__main__':
    # Test with sample data
    from parsers.base_parser import create_parser
    
    sample_files = [
        '/mnt/project/KMA58_DAT.txt',
        '/mnt/project/KMA59_DAT.txt',
        '/mnt/project/KMA57_DAT.txt',
        '/mnt/project/KMA60_DAT.txt',
    ]
    
    lines = []
    for filepath in sample_files:
        parser = create_parser(filepath)
        if parser:
            line = parser.parse(filepath)
            lines.append(line)
            print(f"Parsed: {line.filename}")
    
    # Export to GeoJSON
    output_files = export_network_to_geojson(
        lines, 
        '/home/claude/geodetic_tool/output',
        'test_network'
    )
    
    print("\nExported files:")
    for key, path in output_files.items():
        print(f"  {key}: {path}")
