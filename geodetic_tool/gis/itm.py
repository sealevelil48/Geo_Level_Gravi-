"""
ITM 2005 / Israeli TM Grid (EPSG:2039) -> WGS 84 (EPSG:4326).

Uses pyproj when it is installed (same PROJ pipeline as QGIS).  Otherwise a
built-in conversion: inverse Transverse Mercator on GRS80 (Israel 1993
datum) followed by the 7-parameter Helmert "Israel 1993 to WGS 84 (2)"
(coordinate-frame convention) - the same operation PROJ / QGIS pick, so the
fallback matches them to millimetres.
"""
from __future__ import annotations

import math
from typing import Tuple

# EPSG:2039 projection parameters
_LAT0 = math.radians(31.7343936111111)     # 31 44 03.817 N
_LON0 = math.radians(35.2045169444444)     # 35 12 16.261 E
_K0 = 1.0000067
_FE = 219529.584
_FN = 626907.390
# GRS80
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
# WGS84
_A_W = 6378137.0
_F_W = 1 / 298.257223563
_E2_W = _F_W * (2 - _F_W)
# "Israel 1993 to WGS 84 (2)": translations m, rotations arc-seconds,
# scale ppm, coordinate-frame rotation convention (as in the EPSG / PROJ db)
_TX, _TY, _TZ = 23.772, 17.49, 17.859
_RX, _RY, _RZ = 0.3132, 1.85274, -1.67299
_S_PPM = -5.4262

_transformer = None
_tried_pyproj = False


def _pyproj():
    global _transformer, _tried_pyproj
    if not _tried_pyproj:
        _tried_pyproj = True
        try:
            from pyproj import Transformer
            _transformer = Transformer.from_crs("EPSG:2039", "EPSG:4326", always_xy=True)
        except Exception:
            _transformer = None
    return _transformer


def _meridian_arc(phi: float) -> float:
    e2 = _E2
    e4, e6 = e2 * e2, e2 * e2 * e2
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


def _inverse_tm(e: float, n: float) -> Tuple[float, float]:
    """ITM grid -> (lat, lon) radians on GRS80 (Snyder, Map Projections p.63)."""
    e2 = _E2
    ep2 = e2 / (1 - e2)
    m = _meridian_arc(_LAT0) + (n - _FN) / _K0
    mu = m / (_A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = ep2 * c * c
    t1 = t * t
    n1 = _A / math.sqrt(1 - e2 * s * s)
    r1 = _A * (1 - e2) / (1 - e2 * s * s) ** 1.5
    d = (e - _FE) / (n1 * _K0)
    lat = phi1 - (n1 * t / r1) * (
        d * d / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d ** 6 / 720)
    lon = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d ** 5 / 120) / c
    return lat, lon


def _shift_to_wgs84(lat: float, lon: float) -> Tuple[float, float]:
    """7-parameter Helmert Israel 1993 (GRS80) -> WGS 84 (h = 0)."""
    s = math.sin(lat)
    nu = _A / math.sqrt(1 - _E2 * s * s)
    x0 = nu * math.cos(lat) * math.cos(lon)
    y0 = nu * math.cos(lat) * math.sin(lon)
    z0 = nu * (1 - _E2) * s
    sec = math.pi / (180 * 3600)
    rx, ry, rz = _RX * sec, _RY * sec, _RZ * sec
    m = 1 + _S_PPM * 1e-6
    # coordinate-frame rotation: R = [[1, rz, -ry], [-rz, 1, rx], [ry, -rx, 1]]
    x = _TX + m * (x0 + rz * y0 - ry * z0)
    y = _TY + m * (-rz * x0 + y0 + rx * z0)
    z = _TZ + m * (ry * x0 - rx * y0 + z0)
    lon_w = math.atan2(y, x)
    p = math.hypot(x, y)
    lat_w = math.atan2(z, p * (1 - _E2_W))
    for _ in range(6):                        # Bowring-style iteration
        sw = math.sin(lat_w)
        nu_w = _A_W / math.sqrt(1 - _E2_W * sw * sw)
        h = p / math.cos(lat_w) - nu_w
        lat_w = math.atan2(z, p * (1 - _E2_W * nu_w / (nu_w + h)))
    return lat_w, lon_w


def itm_to_wgs84(easting: float, northing: float) -> Tuple[float, float]:
    """(lon, lat) in degrees, WGS 84, for an EPSG:2039 point."""
    tr = _pyproj()
    if tr is not None:
        lon, lat = tr.transform(easting, northing)
        return float(lon), float(lat)
    lat, lon = _inverse_tm(easting, northing)
    lat, lon = _shift_to_wgs84(lat, lon)
    return math.degrees(lon), math.degrees(lat)
