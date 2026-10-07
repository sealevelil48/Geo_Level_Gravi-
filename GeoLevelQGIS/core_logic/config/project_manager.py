"""
Project Management Module

Handles saving, loading, and managing geodetic leveling projects.
Supports single projects and joint (merged) projects with copy-on-write semantics.
"""
import json
import pickle
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
import logging

from .models import ProjectData, LevelingLine, Benchmark, AdjustmentResult

logger = logging.getLogger(__name__)


class ProjectManager:
    """
    Manages geodetic leveling projects.

    Features:
    - Save/Load projects in JSON or pickle format
    - Create joint projects from multiple sources
    - Copy-on-write: modifications don't affect source projects
    - Project metadata tracking
    """

    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize project manager.

        Args:
            base_path: Base directory for storing projects
        """
        self.base_path = Path(base_path) if base_path else Path.cwd() / "projects"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_project(self, project: ProjectData, format: str = "json") -> str:
        """
        Save a project to disk.

        Args:
            project: ProjectData to save
            format: "json" or "pickle"

        Returns:
            Path to saved file
        """
        if not project.project_path:
            # Generate default path
            sanitized_name = "".join(c for c in project.name if c.isalnum() or c in (' ', '-', '_'))
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            project.project_path = str(self.base_path / f"{sanitized_name}_{timestamp}.{format}")

        filepath = Path(project.project_path)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            self._save_json(project, filepath)
        elif format == "pickle":
            self._save_pickle(project, filepath)
        else:
            raise ValueError(f"Unsupported format: {format}")

        logger.info(f"Project '{project.name}' saved to {filepath}")
        return str(filepath)

    def load_project(self, filepath: str) -> ProjectData:
        """
        Load a project from disk.

        Args:
            filepath: Path to project file

        Returns:
            Loaded ProjectData
        """
        filepath = Path(filepath)

        if not filepath.exists():
            raise FileNotFoundError(f"Project file not found: {filepath}")

        if filepath.suffix == ".json":
            return self._load_json(filepath)
        elif filepath.suffix == ".pickle":
            return self._load_pickle(filepath)
        else:
            raise ValueError(f"Unsupported file format: {filepath.suffix}")

    def create_joint_project(self,
                            name: str,
                            source_project_paths: List[str]) -> ProjectData:
        """
        Create a joint project by merging multiple source projects.

        Uses copy-on-write: all data is deep-copied from sources,
        so modifications won't affect the original projects.

        Args:
            name: Name for the new joint project
            source_project_paths: List of paths to source projects

        Returns:
            New ProjectData containing merged data
        """
        joint_project = ProjectData(
            name=name,
            is_joint_project=True
        )

        for source_path in source_project_paths:
            try:
                source_project = self.load_project(source_path)
                joint_project.merge_from(source_project)
                logger.info(f"Merged project '{source_project.name}' into joint project")
            except Exception as e:
                logger.error(f"Failed to merge project from {source_path}: {e}")

        logger.info(f"Joint project '{name}' created with {len(joint_project.lines)} lines from {len(joint_project.source_projects)} source projects")

        return joint_project

    def _save_json(self, project: ProjectData, filepath: Path):
        """Save project to JSON format (human-readable)."""
        data = {
            'name': project.name,
            'is_joint_project': project.is_joint_project,
            'source_projects': project.source_projects,
            'created': datetime.now().isoformat(),
            'lines': [],
            'benchmarks': {}
        }

        # Serialize lines
        for line in project.lines:
            line_data = {
                'filename': line.filename,
                'start_point': line.start_point,
                'end_point': line.end_point,
                'method': line.method,
                'date': line.date.isoformat() if line.date else None,
                'instrument_id': line.instrument_id,
                'total_distance': line.total_distance,
                'total_height_diff': line.total_height_diff,
                'is_used': line.is_used,
                'original_direction': line.original_direction,
                'setups': []
            }

            for setup in line.setups:
                setup_data = {
                    'setup_number': setup.setup_number,
                    'from_point': setup.from_point,
                    'to_point': setup.to_point,
                    'backsight_reading': setup.backsight_reading,
                    'foresight_reading': setup.foresight_reading,
                    'distance_back': setup.distance_back,
                    'distance_fore': setup.distance_fore,
                    'temperature': setup.temperature,
                    'height_diff': setup.height_diff,
                    'is_used': setup.is_used
                }
                line_data['setups'].append(setup_data)

            data['lines'].append(line_data)

        # Serialize benchmarks
        for point_id, bm in project.benchmarks.items():
            data['benchmarks'][point_id] = {
                'point_id': bm.point_id,
                'height': bm.height,
                'order': bm.order,
                'description': bm.description,
                'easting': bm.easting,
                'northing': bm.northing
            }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_json(self, filepath: Path) -> ProjectData:
        """Load project from JSON format."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        project = ProjectData(
            name=data['name'],
            is_joint_project=data.get('is_joint_project', False),
            source_projects=data.get('source_projects', []),
            project_path=str(filepath)
        )

        # Deserialize lines
        from .models import StationSetup, LineStatus

        for line_data in data.get('lines', []):
            setups = []
            for setup_data in line_data.get('setups', []):
                setup = StationSetup(
                    setup_number=setup_data['setup_number'],
                    from_point=setup_data['from_point'],
                    to_point=setup_data['to_point'],
                    backsight_reading=setup_data['backsight_reading'],
                    foresight_reading=setup_data['foresight_reading'],
                    distance_back=setup_data['distance_back'],
                    distance_fore=setup_data['distance_fore'],
                    temperature=setup_data.get('temperature'),
                    height_diff=setup_data.get('height_diff'),
                    is_used=setup_data.get('is_used', True)
                )
                setups.append(setup)

            line = LevelingLine(
                filename=line_data['filename'],
                start_point=line_data['start_point'],
                end_point=line_data['end_point'],
                setups=setups,
                method=line_data.get('method', 'BF'),
                date=datetime.fromisoformat(line_data['date']) if line_data.get('date') else None,
                instrument_id=line_data.get('instrument_id'),
                total_distance=line_data.get('total_distance', 0.0),
                total_height_diff=line_data.get('total_height_diff', 0.0),
                is_used=line_data.get('is_used', True),
                original_direction=line_data.get('original_direction', 'BF')
            )
            project.add_line(line)

        # Deserialize benchmarks
        for point_id, bm_data in data.get('benchmarks', {}).items():
            bm = Benchmark(
                point_id=bm_data['point_id'],
                height=bm_data['height'],
                order=bm_data.get('order', 3),
                description=bm_data.get('description'),
                easting=bm_data.get('easting'),
                northing=bm_data.get('northing')
            )
            project.add_benchmark(bm)

        return project

    def _save_pickle(self, project: ProjectData, filepath: Path):
        """Save project to pickle format (faster, binary)."""
        with open(filepath, 'wb') as f:
            pickle.dump(project, f)

    def _load_pickle(self, filepath: Path) -> ProjectData:
        """Load project from pickle format."""
        with open(filepath, 'rb') as f:
            project = pickle.load(f)
        project.project_path = str(filepath)
        return project

    def list_projects(self) -> List[Dict[str, str]]:
        """
        List all projects in the base directory.

        Returns:
            List of dictionaries with project metadata
        """
        projects = []

        for file in self.base_path.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                projects.append({
                    'name': data.get('name', file.stem),
                    'path': str(file),
                    'is_joint': data.get('is_joint_project', False),
                    'created': data.get('created', ''),
                    'num_lines': len(data.get('lines', []))
                })
            except Exception as e:
                logger.warning(f"Failed to read project {file}: {e}")

        for file in self.base_path.glob("*.pickle"):
            try:
                with open(file, 'rb') as f:
                    project = pickle.load(f)
                projects.append({
                    'name': project.name,
                    'path': str(file),
                    'is_joint': project.is_joint_project,
                    'created': '',
                    'num_lines': len(project.lines)
                })
            except Exception as e:
                logger.warning(f"Failed to read project {file}: {e}")

        return projects
