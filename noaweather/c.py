"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2020 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from math import hypot, atan2, degrees, exp, log, radians, sin, cos, sqrt, pi
from random import random


class c:
    """Unit conversion  and misc tools"""
    # transition references
    transrefs = {}
    randRefs = {}

    @staticmethod
    def ms2knots(val):
        return val * 1.94384

    @staticmethod
    def kel2cel(val):
        return val - 273.15

    @staticmethod
    def c2p(x, y):
        # Cartesian 2 polar conversion
        r = hypot(x, y)
        a = degrees(atan2(x, y))
        if a < 0:
            a += 360
        if a <= 180:
            a = a + 180
        else:
            a = a - 180
        return a, r

    @staticmethod
    def mb2alt(mb):
        altpress = (1 - (mb / 1013.25) ** 0.190284) * 44307
        return altpress

    @staticmethod
    def oat2msltemp(oat, alt):
        """Converts oat temperature to mean sea level.
        oat in C, alt in meters
        http://en.wikipedia.org/wiki/International_Standard_Atmosphere#ICAO_Standard_Atmosphere
        from FL360 (11km) to FL655 (20km) the temperature deviation stays constant at -71.5degreeC
        from MSL up to FL360 (11km) the temperature decreases at a rate of 6.5degreeC/km
        """
        if alt > 11000:
            return oat + 71.5
        return oat + 0.0065 * alt

    @staticmethod
    def greatCircleDistance(latlong_a, latlong_b):
        """Return the great circle distance of 2 coordinatee pairs"""
        EARTH_RADIUS = 6378137

        lat1, lon1 = latlong_a
        lat2, lon2 = latlong_b

        dLat = radians(lat2 - lat1)
        dLon = radians(lon2 - lon1)
        a = (sin(dLat / 2) * sin(dLat / 2) +
             cos(radians(lat1)) * cos(radians(lat2)) *
             sin(dLon / 2) * sin(dLon / 2))
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        d = EARTH_RADIUS * c
        return d

    @staticmethod
    def interpolate(t1, t2, alt1, alt2, alt):
        if (alt2 - alt1) == 0:
            return t2
        return t1 + (alt - alt1) * (t2 - t1) / (alt2 - alt1)

    @staticmethod
    def expoCosineInterpolate(t1, t2, alt1, alt2, alt, expo=3):
        if alt1 == alt2: return t1
        x = (alt - alt1) / float(alt2 - alt1)
        return t1 + (t2 - t1) * x ** expo

    @staticmethod
    def cosineInterpolate(t1, t2, alt1, alt2, alt):
        if alt1 == alt2: return t1
        x = (alt - alt1) / float(alt2 - alt1)
        return t1 + (t2 - t1) * (0.5 - cos(pi * x) / 2)

    @staticmethod
    def cosineInterpolateHeading(hdg1, hdg2, alt1, alt2, alt):

        if alt1 == alt2: return hdg1

        t2 = c.shortHdg(hdg1, hdg2)
        t2 = c.cosineInterpolate(0, t2, alt1, alt2, alt)
        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def expoCosineInterpolateHeading(hdg1, hdg2, alt1, alt2, alt, expo=3):

        if alt1 == alt2: return hdg1

        t2 = c.shortHdg(hdg1, hdg2)
        t2 = c.expoCosineInterpolate(0, t2, alt1, alt2, alt, expo)
        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def interpolateHeading(hdg1, hdg2, alt1, alt2, alt):
        if alt1 == alt2: return hdg1

        t1 = 0
        t2 = c.shortHdg(hdg1, hdg2)

        t2 = t1 + (alt - alt1) * (t2 - t1) / (alt2 - alt1)

        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def fog2(rh):
        return (80 - rh) / 20 * 24634

    @staticmethod
    def toFloat(string, default=0):
        """Convert to float or return default"""
        try:
            val = float(string)
        except ValueError:
            val = default
        return val

    @staticmethod
    def toInt(string, default=0):
        """Convert to float or return default"""
        try:
            val = int(string)
        except ValueError:
            val = default
        return val

    @staticmethod
    def rh2visibility(rh):
        # http://journals.ametsoc.org/doi/pdf/10.1175/2009JAMC1927.1
        return 1000 * (-5.19 * 10 ** -10 * rh ** 5.44 + 40.10)

    @staticmethod
    def dewpoint2rh(temp, dew):
        return 100 * (exp((17.625 * dew) / (243.04 + dew)) / exp((17.625 * temp) / (243.04 + temp)))

    @staticmethod
    def dewpoint(temp, rh):
        return 243.04 * (log(rh / 100) + ((17.625 * temp) / (243.04 + temp))) / (
                    17.625 - log(rh / 100) - ((17.625 * temp) / (243.04 + temp)))

    @staticmethod
    def shortHdg(a, b):
        if a == 360: a = 0
        if b == 360: b = 0
        if a > b:
            cw = (360 - a + b)
            ccw = -(a - b)
        else:
            cw = -(360 - b + a)
            ccw = (b - a)
        if abs(cw) < abs(ccw):
            return cw
        return ccw

    @staticmethod
    def pa2inhg(pa):
        return pa * 0.0002952998016471232

    @classmethod
    def datarefTransition(cls, dataref, new, elapsed, speed=0.25, id=False):
        """Timed dataref transition"""

        # Save reference to ignore x-plane roundings
        if not id:
            id = str(dataref.DataRef)
        if id not in cls.transrefs:
            cls.transrefs[id] = dataref.value

        # Return if the value is already set
        if cls.transrefs[id] == new:
            return

        current = cls.transrefs[id]

        if current > new:
            dir = -1
        else:
            dir = 1
        if abs(current - new) > speed * elapsed + speed:
            new = current + dir * speed * elapsed

        cls.transrefs[id] = new
        dataref.value = new

    @classmethod
    def transition(cls, new, id, elapsed, speed=0.25):
        """Time based transition """
        if not id in cls.transrefs:
            cls.transrefs[id] = new
            return new

        current = cls.transrefs[id]

        if current > new:
            dir = -1
        else:
            dir = 1
        if abs(current - new) > speed * elapsed + speed:
            new = current + dir * speed * elapsed

        cls.transrefs[id] = new

        return new

    @classmethod
    def transitionClearReferences(cls, refs=False, exclude=False):
        """Clear transition references"""
        if exclude:
            for ref in cls.transrefs.keys():
                if ref.split('-')[0] not in exclude:
                    cls.transrefs.pop(ref)
            return

        elif refs:
            for ref in cls.transrefs.keys():
                if ref.split('-')[0] in refs:
                    cls.transrefs.pop(ref)
        else:
            cls.transrefs = {}

    @classmethod
    def transitionHdg(cls, new, id, elapsed, speed=0.25):
        '''Time based wind heading transition '''

        if not id in cls.transrefs:
            cls.transrefs[id] = new
            return new

        current = cls.transrefs[id]

        diff = c.shortHdg(current, float(new))

        if abs(diff) < speed * elapsed:
            newval = new
        else:
            if diff > 0:
                diff = 1
            else:
                diff = -1
            newval = current + diff * speed * elapsed
            if newval < 0:
                newval += 360
            else:
                newval %= 360

        cls.transrefs[id] = newval
        return newval

    @classmethod
    def datarefTransitionHdg(cls, dataref, new, elapsed, vel=1):
        """Time based wind heading transition"""
        id = str(dataref.DataRef)
        if id not in cls.transrefs:
            cls.transrefs[id] = dataref.value

        if cls.transrefs[id] == new:
            return

        current = cls.transrefs[id]

        diff = c.shortHdg(current, new)
        if abs(diff) < vel * elapsed:
            newval = new
        else:
            if diff > 0:
                diff = +1
            else:
                diff = -1
            newval = current + diff * vel * elapsed
            if newval < 0:
                newval += 360
            else:
                newval %= 360

        cls.transrefs[id] = newval
        dataref.value = newval

    @staticmethod
    def limit(value, max=None, min=None):
        if max is not False and value > max:
            return max
        elif min is not False and value < min:
            return min
        else:
            return value

    @staticmethod
    def cc2xp_old(cover):
        # Cloud cover to X-plane
        xp = int(cover / 100.0 * 4)
        if xp < 1 and cover > 0:
            xp = 1
        elif cover > 89:
            xp = 4
        return xp

    @staticmethod
    def cc2xp(cover):
        # GFS Percent cover to XP
        if cover < 1:
            return 0
        if cover < 30:
            return 1  # 'FEW'
        if cover < 55:
            return 2  # 'SCT'
        if cover < 90:
            return 3  # 'BKN'
        return 4  # 'OVC'

    @staticmethod
    def metar2xpprecipitation(kind, intensity, mod, recent):
        """Return intensity of a metar precipitation"""

        ints = {'-': 0, '': 1, '+': 2}
        intensity = ints[intensity]

        precipitation, friction = False, False

        precip = {
            'DZ': [0.1, 0.2, 0.3],
            'RA': [0.3, 0.5, 0.8],
            'SN': [0.25, 0.5, 0.8],  # Snow
            'SH': [0.7, 0.8, 1]
        }

        wet = {
            'DZ': 1,
            'RA': 1,
            'SN': 2,  # Snow
            'SH': 1,
        }

        if mod == 'SH':
            kind = 'SH'

        if kind in precip:
            precipitation = precip[kind][intensity]
        if recent:
            precipitation = 0
        if kind in wet:
            friction = wet[kind]

        return precipitation, friction

    @staticmethod
    def strFloat(i, false_label='na'):
        """Print a float or na if False"""
        if i is False:
            return false_label
        else:
            return '%.2f' % (i)

    @staticmethod
    def m2ft(n):
        if n is False: return False
        return n * 3.280839895013123

    @staticmethod
    def f2m(n):
        if n is False: return False
        return n * 0.3048

    @staticmethod
    def sm2m(n):
        if n is False: return False
        return n * 1609.344

    @staticmethod
    def m2sm(n):
        if n is False:
            return False
        return n * 0.0006213711922373339

    @staticmethod
    def m2kn(n):
        return n * 1852

    @classmethod
    def convertForInput(cls, value, conversion, toFloat=False, false_str='none'):
        # Make conversion and transform to int
        if value is False:
            value = False
        else:
            convert = getattr(cls, conversion)
            value = convert(value)

        if value is False:
            return false_str

        elif not toFloat:
            value = int(value)
        return str(value)

    @classmethod
    def convertFromInput(cls, string, conversion, default=False, toFloat=False, max=False, min=False):
        # Convert from str and convert
        value = cls.toFloat(string, default)

        if value is False:
            return False

        convert = getattr(cls, conversion)
        value = cls.limit(convert(value), max, min)

        if toFloat:
            return value
        else:
            return int(round(value))

    @classmethod
    def randPattern(cls, id, max_val, elapsed, max_time=1, min_val=0, min_time=1, heading=False):
        """ Creates random cosine interpolated "patterns" """

        if id in cls.randRefs:
            x1, x2, startime, endtime, time = cls.randRefs[id]
        else:
            x1, x2, startime, endtime, time = min_val, 0, 0, 0, 0

        if heading:
            ret = cls.cosineInterpolateHeading(x1, x2, startime, endtime, time)
        else:
            ret = cls.cosineInterpolate(x1, x2, startime, endtime, time)

        time += elapsed

        if time >= endtime:
            # Init randomness
            x2 = min_val + random() * (max_val - min_val)
            t2 = min_time + random() * (max_time - min_time)

            x1 = ret
            startime = time
            endtime = time + t2

        cls.randRefs[id] = x1, x2, startime, endtime, time

        return ret

    @staticmethod
    def middleHeading(hd1, hd2):
        if hd2 > hd1:
            return hd1 + (hd2 - hd1) / 2
        else:
            return hd2 + (360 + hd1 - hd2) / 2

    @staticmethod
    def gfs_levels_help_list():
        """Returns a text list of FL levels with corresponding pressure in millibars"""
        return ["FL%03d %i mb" % (int(c.m2ft(c.mb2alt(i)))/100, i) for i in reversed(range(100, 1050, 50))]
