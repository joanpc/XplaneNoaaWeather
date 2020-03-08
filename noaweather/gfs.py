"""
NOAA weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2015 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import subprocess

from weathersource import GribWeatherSource
from c import c


class GFS(GribWeatherSource):
    """NOAA GFS weather source"""

    base_url = 'https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.'

    download = False
    download_wait = 0

    def __init__(self, conf):
        self.variable_list = conf.gfs_variable_list
        super(GFS, self).__init__(conf)

    @classmethod
    def get_download_url(cls, datecycle, cycle, forecast):
        """Returns the GRIB download url add .idx or .grib to the end"""
        filename = 'gfs.t%02dz.pgrb2full.0p50.f0%02d' % (cycle, forecast)
        url = '%s%s/%02d/%s' % (cls.base_url, datecycle, cycle, filename)

        return url

    @classmethod
    def get_cache_filename(cls, datecycle, cycle, forecast):
        """Returns the proper filename for the cache"""
        return '%s_gfs.t%02dz.pgrb2full.0p50.f0%02d' % (datecycle, cycle, forecast)

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
            kwargs += {'startupinfo': self.conf.spinfo, 'shell': True}

        p = subprocess.Popen([self.conf.wgrib2bin] + args, **kwargs)

        it = iter(p.stdout)
        data = {}
        clouds = {}
        pressure = False
        for line in it:
            r = line[:-1].split(':')
            # Level, variable, value
            level, variable, value = [r[4].split(' '), r[3], r[7].split(',')[2].split('=')[1]]

            if len(level) > 1:
                if level[1] == 'cloud':
                    # cloud layer
                    clouds.setdefault(level[0], {})
                    if len(level) > 3 and variable == 'PRES':
                        clouds[level[0]][level[2]] = value
                    else:
                        # level coverage/temperature
                        clouds[level[0]][variable] = value
                elif level[1] == 'mb':
                    # wind levels
                    data.setdefault(level[0], {})
                    data[level[0]][variable] = value
                elif level[0] == 'mean':
                    if variable == 'PRMSL':
                        pressure = c.pa2inhg(float(value))

        windlevels = []
        cloudlevels = []

        # Let data ready to push on datarefs.

        # Convert wind levels
        for level in data:
            wind = data[level]
            if 'UGRD' in wind and 'VGRD' in wind:
                hdg, vel = c.c2p(float(wind['UGRD']), float(wind['VGRD']))
                # print wind['UGRD'], wind['VGRD'], float(wind['UGRD']), float(wind['VGRD']), hdg, vel
                alt = int(c.mb2alt(float(level)))

                # Optional varialbes
                temp, rh, dew = False, False, False
                # Temperature
                if 'TMP' in wind:
                    temp = float(wind['TMP'])
                # Relative Humidity
                if 'RH' in wind:
                    rh = float(wind['RH'])
                else:
                    temp = False

                if temp and rh:
                    dew = c.dewpoint(temp, rh)

                windlevels.append([alt, hdg, c.ms2knots(vel), {'temp': temp, 'rh': rh, 'dew': dew, 'gust': 0}])
                # print 'alt: %i rh: %i vis: %i' % (alt, float(wind['RH']), vis)

        # Convert cloud level
        for level in clouds:
            level = clouds[level]
            if 'top' in level and 'bottom' in level and 'TCDC' in level:
                top, bottom, cover = float(level['top']), float(level['bottom']), float(level['TCDC'])
                # print "XPGFS: top: %.0fmbar %.0fm, bottom: %.0fmbar %.0fm %d%%" % (top * 0.01, c.mb2alt(top * 0.01), bottom * 0.01, c.mb2alt(bottom * 0.01), cover)

                # if bottom > 1 and alt > 1:
                cloudlevels.append([c.mb2alt(bottom * 0.01) * 0.3048, c.mb2alt(top * 0.01) * 0.3048, cover])
                # XP10
                # cloudlevels.append((c.mb2alt(bottom * 0.01) * 0.3048, c.mb2alt(top * 0.01) * 0.3048, cover/10))

        windlevels.sort()
        cloudlevels.sort()

        data = {
            'winds': windlevels,
            'clouds': cloudlevels,
            'pressure': pressure
        }

        return data
