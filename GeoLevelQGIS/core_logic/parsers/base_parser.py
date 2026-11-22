"""
Base Parser Module

Abstract base class for all file format parsers.
"""
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List, Tuple
import pandas as pd
import logging

from core_logic.config.models import LevelingLine, StationSetup, MeasurementDirection
from core_logic.config.settings import get_settings, FileFormat


logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """Abstract base class for geodetic file parsers."""
    
    def __init__(self, encoding: str = None):
        """
        Initialize the parser.
        
        Args:
            encoding: File encoding to use. If None, uses default from settings.
        """
        self.settings = get_settings()
        self.encoding = encoding or self.settings.encoding.default_encoding
        self.errors: List[str] = []
        self.warnings: List[str] = []
        # Known heights found in the file ({point_id: height}); summary
        # files (.FA0 / .FA1) carry them, field files do not.
        self.benchmarks = {}
    
    @abstractmethod
    def parse(self, filepath: str) -> LevelingLine:
        """
        Parse a file and return a LevelingLine object.
        
        Args:
            filepath: Path to the file to parse
            
        Returns:
            LevelingLine object with parsed data
        """
        pass
    
    @abstractmethod
    def detect_format(self, filepath: str) -> bool:
        """
        Check if this parser can handle the given file format.
        
        Args:
            filepath: Path to the file
            
        Returns:
            True if this parser can handle the file
        """
        pass
    
    def parse_many(self, filepath: str) -> List[LevelingLine]:
        """Parse a file that may hold several lines (summary files).
        Field files (.DAT / .RAW) hold exactly one line."""
        return [self.parse(filepath)]

    def read_file(self, filepath: str) -> List[str]:
        """
        Read file with automatic encoding detection.
        
        Args:
            filepath: Path to the file
            
        Returns:
            List of lines from the file
        """
        encodings = [self.encoding] + self.settings.encoding.fallback_encodings
        
        for enc in encodings:
            try:
                with open(filepath, 'r', encoding=enc) as f:
                    lines = f.readlines()
                logger.debug(f"Successfully read {filepath} with encoding {enc}")
                return lines
            except (UnicodeDecodeError, LookupError):
                continue
        
        # Last resort: read with errors='replace'
        with open(filepath, 'r', encoding='latin-1', errors='replace') as f:
            lines = f.readlines()
        self.warnings.append(f"Could not detect encoding, used latin-1 with replacements")
        return lines
    
    def extract_filename(self, filepath: str) -> str:
        """Extract just the filename without path or extension."""
        return Path(filepath).stem
    
    def parse_to_dataframe(self, filepath: str) -> pd.DataFrame:
        """
        Parse file and return as DataFrame.
        
        Args:
            filepath: Path to the file
            
        Returns:
            pandas DataFrame with measurement data
        """
        line = self.parse(filepath)
        return line.to_dataframe()
    
    def add_error(self, message: str):
        """Add an error message."""
        self.errors.append(message)
        logger.error(message)
    
    def add_warning(self, message: str):
        """Add a warning message."""
        self.warnings.append(message)
        logger.warning(message)
    
    def clear_messages(self):
        """Clear all error and warning messages."""
        self.errors = []
        self.warnings = []


def detect_file_format(filepath: str) -> FileFormat:
    """
    Detect the format of a geodetic data file.
    
    Args:
        filepath: Path to the file
        
    Returns:
        FileFormat enum value
    """
    path = Path(filepath)
    ext = path.suffix.lower()
    filename = path.name.lower()
    
    # Summary files are identified by extension only: their numeric rows
    # can look like Leica GSI words to the content sniffer below.
    if ext == '.rez':
        return FileFormat.REZ
    if ext == '.fa0':
        return FileFormat.FA0
    if ext == '.fa1':
        return FileFormat.FA1

    # Try content-based detection first
    try:
        with open(filepath, 'r', encoding='latin-1') as f:
            first_lines = f.readlines()[:10]
        
        # Check for Trimble DAT format
        for line in first_lines:
            if '|' in line and ('For M5' in line or 'KD1' in line or 'TO' in line):
                return FileFormat.TRIMBLE_DAT
        
        # Check for Leica GSI format
        for line in first_lines:
            line = line.strip()
            if not line:
                continue
            # Leica GSI format has specific patterns
            if line[0:2] == '11' or line[0:2] == '41':
                return FileFormat.LEICA_GSI
            # Check for + pattern typical of GSI
            if '+' in line and len(line) > 20:
                parts = line.split()
                if parts and '+' in parts[0]:
                    return FileFormat.LEICA_GSI
    except:
        pass
    
    # Fall back to extension-based detection
    if ext in ['.dat', '.DAT']:
        return FileFormat.TRIMBLE_DAT
    if ext in ['.raw', '.gsi', '.RAW', '.GSI']:
        return FileFormat.LEICA_GSI
    if ext in ['.rez', '.REZ']:
        return FileFormat.REZ
    if ext in ['.fa0', '.FA0']:
        return FileFormat.FA0

    # Check filename patterns
    if '_dat' in filename or 'dat' in filename:
        return FileFormat.TRIMBLE_DAT
    if '_raw' in filename or 'raw' in filename:
        return FileFormat.LEICA_GSI
    if '_rez' in filename or 'rez' in filename:
        return FileFormat.REZ
    if '_fa0' in filename or 'fa0' in filename:
        return FileFormat.FA0
    if '_fa1' in filename or 'fa1' in filename:
        return FileFormat.FA1

    return FileFormat.UNKNOWN


def create_parser(filepath: str) -> Optional[BaseParser]:
    """
    Factory function to create appropriate parser for a file.

    Args:
        filepath: Path to the file

    Returns:
        Appropriate parser instance or None if format not recognized
    """
    file_format = detect_file_format(filepath)

    if file_format == FileFormat.TRIMBLE_DAT:
        from core_logic.parsers.trimble_parser import TrimbleParser
        return TrimbleParser()
    elif file_format == FileFormat.LEICA_GSI:
        from core_logic.parsers.leica_parser import LeicaParser
        return LeicaParser()
    elif file_format == FileFormat.REZ:
        from core_logic.parsers.rez_parser import RezParser
        return RezParser()
    elif file_format == FileFormat.FA0:
        from core_logic.parsers.fa0_parser import FA0Parser
        return FA0Parser()
    elif file_format == FileFormat.FA1:
        from core_logic.parsers.fa1_parser import FA1Parser
        return FA1Parser()
    else:
        logger.warning(f"Unknown file format for {filepath}")
        return None
