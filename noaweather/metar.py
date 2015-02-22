import re
import os
import sqlite3
import math
import sys
from datetime import datetime, timedelta
import time

from c import c
from asyncdownload import AsyncDownload

class Metar:
    '''
    Metar download and interpretation class
    '''
    # Metar parse regex
    RE_CLOUD        = re.compile('(FEW|BKN|SCT|OVC|VV)([0-9]+)([A-Z][A-Z])?')
    RE_WIND         = re.compile('([0-9]{3})([0-9]{2,3})(G[0-9]{2,3})?(MPH|KT?|MPS)')
    RE_VISIBILITY   = re.compile(' ([0-9]{1,4})(/[0-9])?(SM|KM|M)? ')
    RE_PRESSURE     = re.compile('\b(Q|QNH|SLP|A) ?([0-9]{3,4})\b')
    RE_TEMPERATURE  = re.compile('(M|-)?([0-9]{1,2})/(M|-)?([0-9]{1,2})')
    RE_TEMPERATURE2 = re.compile('T(0|1)([0-9]){3}(0|1)([0-9]){3}')
    RE_PRECIPITATION = re.compile('(-|\+)?(RA|DZ|SN|SG|IC|PL|TS)')
    
    METAR_STATIONS_URL = 'http://www.aviationweather.gov/static/adds/metars/stations.txt'
    METAR_REPORT_URL = 'http://weather.noaa.gov/pub/data/observations/metar/cycles/%sZ.TXT'
    
    UPDATE_RATE = 20 # Redownload metar data every # minutes
    
    STATION_UPDATE_RATE = 30 # In days
    
    def __init__(self, conf, lock):
        
        self.conf = conf
        self.lock = lock
        
        self.cachepath = os.sep.join([conf.cachepath, 'metar'])
        if not os.path.exists(self.cachepath):
            os.makedirs(self.cachepath)
            
        self.database = os.sep.join([self.cachepath, 'metar.db'])
        
        self.th_db = False
                
        # Weather variables
        self.weather = None
        self.reparse = True
        
        # Download flags
        self.ms_download = False
        self.downloading = False
        
        # Main db connection, create db if doens't exist
        createdb = True
        if os.path.exists(self.database):
            createdb = False
        self.connection = self.dbConnect(self.database)
        self.cursor = self.connection.cursor()
        if createdb:
            self.conf.ms_update = 0
            self.dbCreate(self.connection)
        
        # Metar stations update
        if (time.time() - self.conf.ms_update) > self.STATION_UPDATE_RATE * 86400:
            self.ms_download = AsyncDownload(self.conf, self.METAR_STATIONS_URL, os.sep.join(['metar', 'stations.txt']))
            
        self.last_latlon, self.last_station, self.last_timestamp = [False]*3
        
    def dbConnect(self, path):
        return sqlite3.connect(path, check_same_thread=False)
        
    def dbCreate(self, db):
        cursor = db.cursor()
        cursor.execute('''CREATE TABLE airports (icao text KEY UNIQUE, lat real, lon real, elevation int,
                        timestamp int KEY, metar text)''')
        db.commit()
    
    def updateStations(self, db, path):
        ''' Updates aiports db from metar stations file'''
        self.conf.ms_update = time.time()
        
        cursor = db.cursor()
        
        f = open(path, 'r')
        n = 0

        for line in f.readlines():
            if line[0] != '!' and len(line) > 80:
                icao = line[20:24]
                lat = float(line[39:41]) + round(float(line[42:44])/60, 4)
                if line[44] == 'S':
                    lat *= -1
                lon = float(line[47:50]) + round(float(line[51:53])/60, 4)
                if line[53] == 'W':
                    lon *= -1
                elevation = int(line[55:59])
                if line[20] != ' ' and line[51] != '9':
                    cursor.execute('INSERT OR REPLACE INTO airports (icao, lat, lon, elevation, timestamp) VALUES (?,?,?,?,0)', 
                                   (icao.strip('"'), lat, lon, elevation))
                n += 1
        
        db.commit()
        return n
        
    def updateMetar(self, db, path):
        ''' Updates metar table from Metar file'''
        f = open(path, 'r')
        new, updated = 0, 0
        timestamp = 0
        cursor = db.cursor()
        i = 0
        inserts = []
        INSBUF = 128
        for line in f.readlines():
            
            if line[0].isalpha():
                i += 1
                icao, mtime, metar = line[0:4], int(line[5:11]) , re.sub(r'[^\x00-\x7F]+',' ', line[5:-1])
                inserts.append((timestamp, metar, icao, timestamp))
                  
                if (i % INSBUF) == 0:
                    sys.stdout.flush()
                    cursor.executemany('UPDATE airports SET timestamp = ?, metar = ? WHERE icao = ? AND timestamp < ?', inserts)
                    inserts = []
            elif len(line) > 15:
                timestamp = int(line[0:4] + line[5:7] + line[8:10] + line[11:13] + line[14:16])
        
        if len(inserts):
            cursor.executemany('UPDATE airports SET timestamp = ?, metar = ? WHERE icao = ? AND timestamp < ?', inserts)
        db.commit()
        return (new, updated)  
        
    def getClosestStation(self, db, lat, lon, limit = 1):
        ''' Return closest airport with a metar report'''
        
        cursor = db.cursor()
        fudge = math.pow(math.cos(math.radians(lat)),2)
        res = cursor.execute('''SELECT * FROM airports
                                WHERE metar NOT NULL
                                ORDER BY ((? - lat) * (? - lat) + (? - lon) * (? - lon) * ?) 
                                LIMIT ?''', (lat, lat, lon, lon, fudge, limit))
        ret = res.fetchall()
        if limit == 1 and len(ret) > 0:
            return ret[0]
        return ret
    
    def getMetar(self, db, icao):
        ''' Get metar from icao name '''
        cursor = db.cursor()
        res = cursor.execute('''SELECT * FROM airports
                                WHERE icao = ? AND metar NOT NULL LIMIT 1''', (icao, ))
        ret = res.fetchall()
        if len(ret) > 0:
            return ret[0]
        return ret
    
    def getCycle(self):
        now = datetime.utcnow()
        # Cycle is updated until the houre has arrived (ex: 01 cycle updates until 1am)
        cnow = now - timedelta(hours=0, minutes=15)
        # Get last cycle
        return ('%02d' % cnow.hour, int(time.time())/60/self.UPDATE_RATE)
    
    def parseMetar(self, icao, metar, airport_msl = 0):
        ''' Parse metar'''
        
        weather = {
                   'icao': icao,
                   'metar': metar,
                   'elevation': airport_msl,
                   'wind': [0, 0, 0], # Heading, speed, shear
                   'clouds': [0, 0, False] * 3, # Alt, coverage type
                   'temperature': [0, 0], # Temperature, dewpoint
                   'pressure': c.pa2inhg(1013.25),
                   'visibility': 9999,
                   'precipitation': 0,
                   }
        
        clouds = []
        
        for cloud in self.RE_CLOUD.findall(metar):
            coverage, alt, type = cloud
            alt = float(alt) * 30.48 + airport_msl 
            clouds.append([alt, coverage, type])
        
        weather['clouds'] = clouds
        
        m = self.RE_PRESSURE.search(metar)
        if m:
            unit, press = m.groups()
            press = float(press)
            
            if unit in ('A'):
                # inHg
                press = press/100
            else:
                # unit Q
                press = c.pa2inhg(press * 100)
            
            weather['pressure'] = press
        
        m = self.RE_TEMPERATURE2.search(metar)
        if m:
            tp, temp, dp, dew = m.groups()
            temp = float(temp) * 0.1
            dew = float(dew) * 0.1
            if tp == '0': temp *= -1
            if dp == '0': dew *= -1
            weather['temperature'] = [temp, dew]  
        else:  
            m = self.RE_TEMPERATURE.search(metar)
            if m:
                temps, temp, dews, dew = m.groups()
                temp = int(temp)
                dew = int(dew)
                if dews: dew *= -1
                if temps: temp *= -1
                weather['temperature'] = [temp, dew]
        
        m = self.RE_VISIBILITY.search(metar)
        if m:
            visibility, div, unit = int(m.group(1)), m.group(2), m.group(3)
            if unit == 'SM': visibility *= 1609.34
            if unit == 'KM': visibility *= 1000
            if div: visibility /= float(div[1:])
            
            weather['visibility'] = visibility
        
        m = self.RE_WIND.search(metar)
        if m:
            heading, speed, gust, unit = m.groups()
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
                    gust  /= 60
                                
            weather['wind'] = [heading, speed, gust]
            
        precipitation = {}
        for precp in self.RE_PRECIPITATION.findall(metar):
            intensity, type = precp
            precipitation[type] = intensity
            
        weather['precipitation'] = precipitation
    
        return weather
    
    def run(self, lat, lon, rate):
        
        # Worker thread requires it's own db connection and cursor
        if not self.th_db:
            self.th_db = self.dbConnect(self.database)
        
        # Check for new metar dowloaded data
        if self.downloading == True:
            if not self.download.q.empty():
                
                self.downloading = False
                metarfile = self.download.q.get()
                
                if metarfile:
                    self.updateMetar(self.th_db, os.sep.join([self.conf.cachepath, metarfile]))
                    self.reparse = True
                else:
                    # No file downloaded
                    pass
        
        else:
            # Download new data if required
            cycle, timestamp = self.getCycle()
            if self.last_timestamp != timestamp:
                self.last_timestamp = timestamp
                self.downloadCycle(cycle, timestamp)
                
        # Update stations table if required
        if self.ms_download and not self.ms_download.q.empty():
            self.updateStations(self.th_db, os.sep.join([self.conf.cachepath, self.ms_download.q.get()]))
            self.ms_download = False
            
    def downloadCycle(self, cycle, timestamp):
        self.downloading = True
        
        cachepath = os.sep.join([self.conf.cachepath, 'metar'])
        if not os.path.exists(cachepath):
            os.makedirs(cachepath)
        
        cachefile = os.sep.join(['metar', '%d_%sZ.txt' % (timestamp, cycle)])
        url = self.METAR_REPORT_URL % (cycle)
        self.download = AsyncDownload(self.conf, url, cachefile)
        
    def die(self):
        self.connection.commit()
        self.connection.close()