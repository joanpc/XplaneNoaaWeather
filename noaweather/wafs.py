"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2015 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import subprocess
from datetime import datetime, timedelta

from weathersource import GribWeatherSource

from c import c


class WAFS(GribWeatherSource):
    """World Area Forecast System - Upper Air Forecast weather source"""

    cycles = [0, 6, 12, 18]
    forecasts = [6, 9, 12, 15, 18, 21, 24]
    baseurl = 'https://www.ftp.ncep.noaa.gov/data/nccf/com/gfs/prod'

    download_wait = 0
    publish_delay = {'hours': 5, 'minutes': 0}
    grib_conf_var = 'lastwafsgrib'

    def __init__(self, conf):
        super(WAFS, self).__init__(conf)

    def run(self, elapsed):
        """DISABLED: WAFS is not freely available anymore."""
        pass

    @classmethod
    def get_cycle_date(cls):
        """Returns last cycle date available"""
        now = datetime.utcnow()

        cnow = now - timedelta(**cls.publish_delay)
        # Get last cycle
        for cycle in cls.cycles:
            if cnow.hour >= cycle:
                lcycle = cycle
        # Forecast
        adjs = 0
        if cnow.day != now.day:
            adjs = +24
        # Elapsed from cycle
        forecast = (adjs + now.hour - lcycle)
        # Get current forecast
        for fcast in cls.forecasts:
            if forecast <= fcast:
                forecast = fcast
                break

        return '%d%02d%02d%02d' % (cnow.year, cnow.month, cnow.day, lcycle), lcycle, forecast

    def parse_grib_data(self, filepath, lat, lon):
        """Executes wgrib2 and parses its output"""

        args = ['-s',
                '-lon',
                '%f' % (lon),
                '%f' % (lat),
                filepath
                ]

        kwargs = {'stdout': subprocess.PIPE}

        if self.conf.spinfo:
            kwargs.update({'startupinfo': self.conf.spinfo, 'shell': True})

        p = subprocess.Popen([self.conf.wgrib2bin] + args, **kwargs)

        it = iter(p.stdout)

        cat = {}
        for line in it:
            r = line[:-1].split(':')
            # Level, variable, value
            level, variable, value, maxave = [r[4].split(' '), r[3], r[7].split(',')[2].split('=')[1], r[6]]
            if len(level) > 1 and level[1] == 'mb' and maxave == 'spatial max':
                # print level[1], variable, value
                alt = int(c.mb2alt(float(level[0])))
                value = float(value)
                if value < 0:
                    value = 0
                if variable == 'CTP':
                    value *= 100
                if variable in ('CAT', 'CTP'):
                    if alt in cat:
                        # override existing value if bigger
                        if value > cat[alt]:
                            cat[alt] = value
                    else:
                        cat[alt] = value

        turbulence = []
        for key, value in cat.iteritems():
            turbulence.append([key, value / 6.0])
        turbulence.sort()

        return turbulence

    @classmethod
    def get_download_url(cls, datecycle, cycle, forecast):
        filename = "WAFS_blended_%sf%02d.grib2" % (datecycle, forecast)
        url = "%s/gfs.%s/%s/%s" % (cls.baseurl, datecycle[:-2], datecycle[-2:], filename)
        return url

    @classmethod
    def get_cache_filename(cls, datecycle, cycle, forecast):
        filename = "%s_wafs.WAFS_blended_%sf%02d.grib2" % (datecycle, datecycle, forecast)
        return filename
