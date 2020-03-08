"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2020 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import re
import os
import sqlite3
import math
import sys
from datetime import datetime, timedelta
import time
from util import util

from c import c
from weathersource import WeatherSource
from weathersource import GribDownloaderError
from weathersource import GribDownloader
from weathersource import AsyncTask


class Metar(WeatherSource):
    """Provides METAR download and parsing routines. """

    # Metar parse regex
    RE_CLOUD = re.compile(r'\b(?P<coverage>FEW|BKN|SCT|OVC|VV)(?P<level>[0-9]+)(?P<type>[A-Z]{2,3})?\b')
    RE_WIND = re.compile(r'\b(?P<heading>VRB|[0-9]{3})(?P<speed>[0-9]{2,3})'
                         r'(?P<gust>G[0-9]{2,3})?(?P<unit>MPH|KT?|MPS|KMH)\b')
    RE_VARIABLE_WIND = re.compile(r'\b(?P<from_heading>[0-9]{3})V(?P<to_heading>[0-9]{3})\b')
    RE_VISIBILITY = re.compile(r'\b(CAVOK|[PM]?([0-9]{4})|([0-9] )?([0-9]{1,2})(/[0-9])?(SM|KM))\b')
    RE_PRESSURE = re.compile(r'\b(Q|QNH|SLP|A)[ ]?([0-9]{3,4})\b')
    RE_TEMPERATURE = re.compile(r'\b([M-])?([0-9]{1,2})/([M-])?([0-9]{1,2})\b')
    RE_TEMPERATURE2 = re.compile(r'\bT([01])([0-9]{3})([01])([0-9]{3})\b')
    RE_PRECIPITATION = re.compile(r'(?P<intensity>[-+])?(?P<recent>RE)?(?P<modifier>DZ|SG|IC|PL|SH)?'
                                  r'(?P<kind>DZ|RA|SN|TS)(?P<negation>NO|E)?')
    RE_RVR = re.compile(r'R(?P<runway>(?P<heading>[0-9]{2})(?P<rw_position>[LCR])?)/'
                        r'(?P<exceed>[PM])?(?P<visibility>[0-9]{4})(?P<change>[UDN])?')

    METAR_STATIONS_URL = 'https://www.aviationweather.gov/docs/metar/stations.txt'
    NOAA_METAR_URL = 'https://aviationweather.gov/adds/dataserver_current/current/metars.cache.csv.gz'
    VATSIM_METAR_URL = 'https://metar.vatsim.net/metar.php?id=all'
    IVAO_METAR_URL = 'https://wx.ivao.aero/metar.php'

    STATION_UPDATE_RATE = 30  # In days

    def __init__(self, conf):

        self.cache_path = os.sep.join([conf.cachepath, 'metar'])
        self.database = os.sep.join([self.cache_path, 'metar.db'])

        super(Metar, self).__init__(conf)

        self.th_db = False

        # Download flags
        self.ms_download = False
        self.downloading = False

        self.next_metarRWX = time.time() + 30

        # Main db connection, create db if doens't exist
        createdb = True
        if os.path.isfile(self.database):
            createdb = False

        self.connection = self.db_connect(self.database)
        self.cursor = self.connection.cursor()
        if createdb:
            conf.ms_update = 0
            self.db_create(self.connection)

        # Metar stations update
        if (time.time() - conf.ms_update) > self.STATION_UPDATE_RATE * 86400:
            self.ms_download = AsyncTask(GribDownloader.download, self.METAR_STATIONS_URL, 'stations.txt',
                                         cancel_event=self.die)
            self.ms_download.start()

        self.last_timestamp = 0

    def db_connect(self, path):
        """Returns an SQLite connection to the metar database"""
        return sqlite3.connect(path, check_same_thread=False)

    def db_create(self, db):
        """Creates the METAR database and tables"""
        cursor = db.cursor()
        cursor.execute('''CREATE TABLE airports (icao text KEY UNIQUE, lat real, lon real, elevation int,
                        timestamp int KEY, metar text)''')
        db.commit()

    def update_stations(self, db, path):
        """Updates db's airport information from the METAR station file"""

        cursor = db.cursor()
        parsed = 0

        with open(path, 'r') as f:
            try:
                for line in f.readlines():
                    if line[0] != '!' and len(line) > 80:
                        icao = line[20:24]
                        lat = float(line[39:41]) + round(float(line[42:44]) / 60, 4)
                        if line[44] == 'S':
                            lat *= -1
                        lon = float(line[47:50]) + round(float(line[51:53]) / 60, 4)
                        if line[53] == 'W':
                            lon *= -1
                        elevation = int(line[55:59])
                        if line[20] != ' ' and line[51] != '9':
                            cursor.execute(
                                'INSERT OR REPLACE INTO airports (icao, lat, lon, elevation, timestamp) \
                                 VALUES (?,?,?,?,0)',
                                (icao.strip('"'), lat, lon, elevation))
                            parsed += 1
            except (ValueError, IndexError) as e:
                print "Error parsing METAR station File: %s" % str(e)

            db.commit()
            self.conf.ms_update = time.time()

        return parsed

    def update_metar(self, db, path):
        """Updates metar table from Metar file"""
        f = open(path, 'r')
        nupdated = 0
        nparsed = 0
        timestamp = 0
        cursor = db.cursor()
        i = 0
        inserts = []
        INSBUF = cursor.arraysize

        today_prefix = datetime.utcnow().strftime('%Y%m')
        yesterday_prefix = (datetime.utcnow() + timedelta(days=-1)).strftime('%Y%m')

        today = datetime.utcnow().strftime('%d')

        for line in f.readlines():
            if line[0].isalpha() and len(line) > 11 and line[11] == 'Z':
                i += 1
                icao, mtime, metar = line[0:4], line[5:11], re.sub(r'[^\x00-\x7F]+', ' ', line[5:-1])
                metar = metar.split(',')[0]

                if mtime[-1] == 'Z':
                    mtime = '0' + mtime[:-1]

                if not mtime.isdigit():
                    mtime = '000000'

                # Prepend year and month to the timestamp
                if mtime[:2] == today:
                    timestamp = today_prefix + mtime
                else:
                    timestamp = yesterday_prefix + mtime

                inserts.append((timestamp, metar, icao, timestamp))
                nparsed += 1
                timestamp = 0

                if (i % INSBUF) == 0:
                    cursor.executemany('UPDATE airports SET timestamp = ?, metar = ? WHERE icao = ? AND timestamp < ?',
                                       inserts)
                    inserts = []
                    nupdated += cursor.rowcount

        if len(inserts):
            cursor.executemany('UPDATE airports SET timestamp = ?, metar = ? WHERE icao = ? AND timestamp < ?', inserts)
            nupdated += cursor.rowcount
        db.commit()

        f.close()

        if not self.conf.keepOldFiles:
            util.remove(path)

        return nupdated, nparsed

    @staticmethod
    def clear_reports(db):
        """Clears all metar reports from the db"""
        cursor = db.cursor()
        cursor.execute('UPDATE airports SET metar = NULL, timestamp = 0')
        db.commit()

    def get_closest_station(self, db, lat, lon, limit=1):
        """Return the closest airport with a metar report"""

        cursor = db.cursor()
        fudge = math.pow(math.cos(math.radians(lat)), 2)

        if self.conf.ignore_metar_stations:

            q = '''SELECT * FROM airports
                                    WHERE metar NOT NULL AND icao NOT in (%s)
                                    ORDER BY ((? - lat) * (? - lat) + (? - lon) * (? - lon) * ?)
                                    LIMIT ?''' % (','.join(['?'] * len(self.conf.ignore_metar_stations)))

            res = cursor.execute(q, tuple(self.conf.ignore_metar_stations) + (lat, lat, lon, lon, fudge, limit))

        else:
            res = cursor.execute('''SELECT * FROM airports
                                    WHERE metar NOT NULL
                                    ORDER BY ((? - lat) * (? - lat) + (? - lon) * (? - lon) * ?)
                                    LIMIT ?''', (lat, lat, lon, lon, fudge, limit))

        ret = res.fetchall()
        if limit == 1 and len(ret) > 0:
            return ret[0]
        return ret

    @staticmethod
    def get_metar(db, icao):
        """Returns the METAR from an airport icao code"""
        cursor = db.cursor()
        res = cursor.execute('''SELECT * FROM airports
                                WHERE icao = ? AND metar NOT NULL LIMIT 1''', (icao.upper(),))

        ret = res.fetchall()
        if len(ret) > 0:
            return ret[0]
        return ret

    @staticmethod
    def get_current_cycle():
        """Returns the current METAR cycle"""
        now = datetime.utcnow()
        # Cycle is updated until the hour has arrived (ex: 01 cycle updates until 1am)

        current_cycle = now + timedelta(hours=0, minutes=5)

        timestamp = int(time.time())
        return ('%02d' % current_cycle.hour, timestamp)

    @classmethod
    def parse_metar(cls, icao, metar, airport_msl=0):
        """Returns a parsed METAR"""

        weather = {
            'icao': icao,
            'metar': metar,
            'elevation': airport_msl,
            'wind': [0, 0, 0],  # Heading, speed, shear
            'variable_wind': False,
            'clouds': [0, 0, False] * 3,  # Alt, coverage type
            'temperature': [False, False],  # Temperature, dewpoint
            'pressure': False,  # space c.pa2inhg(10.1325),
            'visibility': 9998,
            'precipitation': {},
            'rvr': []
        }

        metar = metar.split('TEMPO')[0]

        clouds = []

        for cloud in cls.RE_CLOUD.findall(metar):
            coverage, alt, type = cloud
            alt = float(alt) * 30.48 + airport_msl
            clouds.append([alt, coverage, type])

        weather['clouds'] = clouds

        m = cls.RE_PRESSURE.search(metar)
        if m:
            unit, press = m.groups()
            press = float(press)

            if unit:
                if unit == 'A':
                    press = press / 100
                elif unit == 'SLP':
                    if press > 500:
                        press = c.pa2inhg((press / 10 + 900) * 100)
                    else:
                        press = c.pa2inhg((press / 10 + 1000) * 100)
                elif unit == 'Q':
                    press = c.pa2inhg(press * 100)

            if 25 < press < 35:
                weather['pressure'] = press

        m = cls.RE_TEMPERATURE2.search(metar)
        if m:
            tp, temp, dp, dew = m.groups()
            temp = float(temp) * 0.1
            dew = float(dew) * 0.1
            if tp == '1': temp *= -1
            if dp == '1': dew *= -1
            weather['temperature'] = [temp, dew]
        else:
            m = cls.RE_TEMPERATURE.search(metar)
            if m:
                temps, temp, dews, dew = m.groups()
                temp = int(temp)
                dew = int(dew)
                if dews: dew *= -1
                if temps: temp *= -1
                weather['temperature'] = [temp, dew]

        metar = metar.split('RMK')[0]

        m = cls.RE_VISIBILITY.search(metar)
        if m:
            if m.group(0) == 'CAVOK' or (m.group(0)[0] == 'P' and int(m.group(2)) > 7999):
                visibility = 9999
            else:
                visibility = 0

                vis0, vis1, vis2, vis3, div, unit = m.groups()

                if vis1: visibility += int(vis1)
                if vis2: visibility += int(vis2)
                if vis3:
                    vis3 = int(vis3)
                    if div:
                        vis3 /= float(div[1:])
                    visibility += vis3
                if unit == 'SM': visibility *= 1609.34
                if unit == 'KM': visibility *= 1000

            weather['visibility'] = visibility

        m = cls.RE_WIND.search(metar)
        if m:
            heading, speed, gust, unit = m.groups()
            if heading == 'VRB':
                heading = 0
                weather['variable_wind'] = [0, 360]
            else:
                heading = int(heading)

            speed = int(speed)
            if not gust:
                gust = 0
            else:
                gust = int(gust[1:]) - speed

            if unit in ('MPS', 'MPH'):
                speed = c.ms2knots(speed)
                gust = c.ms2knots(gust)
                if unit == 'MPH':
                    speed /= 60
                    gust /= 60
            if unit == 'KMH':
                speed = c.m2kn(speed / 1000.0)
                gust = c.m2kn(gust / 1000.0)

            weather['wind'] = [heading, speed, gust]

        m = cls.RE_VARIABLE_WIND.search(metar)
        if m:
            h1, h2 = m.groups()
            weather['variable_wind'] = [int(h1), int(h2)]

        precipitation = {}
        for precp in cls.RE_PRECIPITATION.findall(metar):
            intensity, recent, mod, kind, neg = precp
            if neg == 'E':
                recent = 'RE'
            if neg != 'NO':
                precipitation[kind] = {'int': intensity, 'mod': mod, 'recent': recent}

        weather['precipitation'] = precipitation

        for rvr in cls.RE_RVR.finditer(metar):
            r = dict(rvr.groupdict())
            r['heading'] = int(r['heading']) * 10
            r['visibility'] = int(r['visibility'])
            weather['rvr'].append(r)

        # Extended visibility
        if weather['visibility'] > 9998:
            weather['mt_visibility'] = weather['visibility']
            ext_vis = c.rh2visibility(c.dewpoint2rh(weather['temperature'][0], weather['temperature'][1]))
            if ext_vis > weather['visibility']:
                weather['visibility'] = int(ext_vis)

        return weather

    def run(self, elapsed):

        # Worker thread requires it's own db connection and cursor
        if not self.th_db:
            self.th_db = self.db_connect(self.database)

        # Check for new metar downloaded data
        if self.download:
            if not self.download.pending():

                metar_file = self.download.result
                self.download.join()
                if isinstance(metar_file, GribDownloaderError):
                    print "Error downloading METAR: %s" % str(metar_file)
                else:
                    print 'Successfully downloaded: %s' % metar_file.split(os.path.sep)[-1]
                    updated, parsed = self.update_metar(self.th_db, metar_file)
                    print "METAR updated/parsed: %d/%d" % (updated, parsed)

                self.download = False

        elif self.conf.download:
            # Download new data if required
            cycle, timestamp = self.get_current_cycle()
            if (timestamp - self.last_timestamp) > self.conf.metar_updaterate * 60:
                self.last_timestamp = timestamp
                self.download_cycle(cycle, timestamp)

        # Update stations table if required
        if self.ms_download and not self.ms_download.pending():
            stations = self.ms_download.result

            if isinstance(stations, GribDownloaderError):
                print "Error downloading metar stations file %s" % stations.message

            else:
                print 'Updating metar stations.'
                nstations = self.update_stations(self.th_db, self.ms_download.result)
                print '%d metar stations updated.' % nstations
            self.ms_download = False

        # Update METAR.rwx
        if self.conf.updateMetarRWX and self.next_metarRWX < time.time():
            if self.update_metar_rwx_file(self.th_db):
                self.next_metarRWX = time.time() + 300
                print 'Updated METAR.rwx file.'
            else:
                # Retry in 10 sec
                self.next_metarRWX = time.time() + 10

    def download_cycle(self, cycle, timestamp):
        self.downloading = True

        if not os.path.exists(self.cache_path):
            os.makedirs(self.cache_path)

        prefix = self.conf.metar_source

        if self.conf.metar_source == 'NOAA':
            url = self.NOAA_METAR_URL

        elif self.conf.metar_source == 'VATSIM':
            url = self.VATSIM_METAR_URL

        elif self.conf.metar_source == 'IVAO':
            url = self.IVAO_METAR_URL

        cache_file = os.path.sep.join([self.cache_path, '%s_%d_%sZ.txt' % (prefix, timestamp, cycle)])
        print "Downloading METAR: %s" % cache_file.split(os.path.sep)[-1]
        self.download = AsyncTask(GribDownloader.download, url, cache_file, cancel_event=self.die)
        self.download.start()

    def update_metar_rwx_file(self, db):
        """Dumps all metar data to the METAR.rwx file"""

        cursor = db.cursor()

        try:
            f = open(os.sep.join([self.conf.syspath, 'METAR.rwx']), 'w')
        except OSError:
            print "ERROR updating METAR.rwx file: %s %s" % (sys.exc_info()[0], sys.exc_info()[1])
            return False

        res = cursor.execute('SELECT icao, metar FROM airports WHERE metar NOT NULL')

        while True:
            rows = res.fetchmany()
            if rows:
                for row in rows:
                    f.write('%s %s\n' % (row[0], row[1]))
            else:
                break

        f.close()
        return True

    def shutdown(self):
        super(Metar, self).shutdown()
        self.connection.commit()
        self.connection.close()
