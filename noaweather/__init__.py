import sys
if sys.version_info == 2:
    from noaweather.c import c
    from noaweather.conf import Conf
    from noaweather.weathersource import WeatherSource
    from noaweather.gfs import GFS
    from noaweather.metar import Metar
    from noaweather.wafs import WAFS
    from noaweather.EasyDref import EasyDref
    from noaweather.EasyDref import EasyCommand
    from noaweather.tracker import Tracker
else:
    from .c import c
    from .conf import Conf
    from .weathersource import WeatherSource
    from .gfs import GFS
    from .metar import Metar
    from .wafs import WAFS
    try:
        from .EasyDref import EasyDref
        from .EasyDref import EasyCommand
        from .tracker import Tracker
    except ImportError:
        pass
