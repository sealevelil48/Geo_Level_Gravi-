def classFactory(iface):
    from .geo_level_plugin import GeoLevelPlugin
    return GeoLevelPlugin(iface)
