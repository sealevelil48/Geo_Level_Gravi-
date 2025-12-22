"""
Base Parser Module

Abstract base class for all file format parsers.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List, Tuple
import pandas as pd
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.models import LevelingLine, StationSetup, MeasurementDirection
from config.settings import get_settings, FileFormat


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
    
    # Check filename patterns
    if '_dat' in filename or 'dat' in filename:
        return FileFormat.TRIMBLE_DAT
    if '_raw' in filename or 'raw' in filename:
        return FileFormat.LEICA_GSI
    
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
        from parsers.trimble_parser import TrimbleParser
        return TrimbleParser()
    elif file_format == FileFormat.LEICA_GSI:
        from parsers.leica_parser import LeicaParser
        return LeicaParser()
    else:
        logger.warning(f"Unknown file format for {filepath}")
        return None
