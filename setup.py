from setuptools import setup, find_packages

setup(
    name="geodetic_tool",
    version="1.1.0",
    packages=find_packages(),
    package_data={"geodetic_tool": ["resources/*.json"]},
    install_requires=[
        "pandas>=1.3.0",
        "numpy>=1.20.0",
    ],
    extras_require={
        "db": ["psycopg2-binary>=2.9", "keyring>=23"],
        "gis": ["pyproj>=3.0"],
    },
    entry_points={
        "console_scripts": [
            "geodetic-cli=geodetic_tool.cli.main:main",
        ],
    },
    python_requires=">=3.8",
    author="Geodetic Tools",
    description="Geodetic leveling data processing tool",
)
