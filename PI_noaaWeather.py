'''
X-plane NOAA GFS weather plugin.

Sets x-plane wind and cloud layers using NOAA real/forecast data.
This plugin downloads required data from NOAA servers.

Uses wgrib2 to parse NOAA grib2 data files.
Includes wgrib2 binaries for Mac Win32 and linux i386glibc6
Win32 wgrib2 requires cgywin now included in the resources folder

This plugin is under developement and INCOMPLETE

TODO:
- Remove shear and turbulence on transition
- Turbulences, rain, snow, wind shears, visibility
- msl pressure
- clear cache
- remove old grib files from cache

Copyright (C) 2012  Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
'''
__VERSION__ = '1.5.0'

#Python includes
from datetime import datetime, timedelta
import threading
from math import hypot, atan2, degrees, exp
import os
import sys
from time import sleep
import cPickle
import multiprocessing

from urllib import urlretrieve
import subprocess

#### stderr stdout ebeded python workaround ####
class wr_stdout():
    write = sys.stdout.write
    def flush(self):
        pass
class wr_stderr():
    write = sys.stderr.write
    def flush(self):
        pass
sys.stdout = wr_stdout()
sys.stderr = wr_stderr()
# Argv missing on windows
sys.argv = ['']
###  ############################################

class AsyncDownload():
    '''
    Asyncronous download
    '''
    def __init__(self, conf, url, cachefile):
        self.q = multiprocessing.Queue()
        self.dirsep = conf.dirsep[:]
        cachepath = conf.cachepath[:]
        self.wgrib2bin = conf.wgrib2bin[:]
        if sys.platform == 'win32':
            multiprocessing.set_executable(os.path.join(sys.exec_prefix, 'pythonw.exe'))
        self.child = multiprocessing.Process(target=self.run, args=(url, cachepath, cachefile))
        self.child.start()
        
    def run(self, url, cachepath, cachefile):
        filepath = cachepath + "/" + cachefile
        tempfile = filepath + '.tmp'
        urlretrieve(url, tempfile)
        
        if os.path.getsize(tempfile) > 500:
            # Downloaded
            # unpack grib file
            subprocess.call([self.wgrib2bin, tempfile, '-set_grib_type', 'simple', '-grib_out', filepath])
            os.remove(tempfile)
            self.q.put(cachefile)
        else:
            # File unavaliable, empty file; wait 5 minutes
            #print "XPGFS: Error downloading: %s" % (self.cachefile)
            if os.path.exists(tempfile):
                os.remove(tempfile)
            self.q.put(False)

# Detect x-plane plugin
if sys.platform != 'win32' or 'plane' in sys.executable.lower():
                    
    # X-plane includes
    from XPLMDefs import *
    from XPLMProcessing import *
    from XPLMDataAccess import *
    from XPLMUtilities import *
    from XPLMPlanes import *
    from XPLMNavigation import *
    from SandyBarbourUtilities import *
    from PythonScriptMessaging import *
    from XPLMPlugin import *
    from XPLMMenus import *
    from XPWidgetDefs import *
    from XPWidgets import *
    from XPStandardWidgets import *
    from EasyDref import EasyDref
    
    class c:
        '''
        Conversion tools
        '''
        @classmethod
        def ms2knots(self, val):
            return val * 1.94384
        
        @classmethod
        def kel2cel(self, val):
            return val - 273.15
        
        @classmethod
        def c2p(self, x, y):
            #Cartesian 2 polar conversion
            r = hypot(x, y)
            a = degrees(atan2(x, y))
            if a < 0:
                a += 360
            if a <= 180:
                a = a + 180
            else:
                a = a -180
            return a, r
        
        @classmethod
        def mb2alt(self, mb):
            altpress = 44330.8 - (4946.54 * (mb*100)**0.1902632)
            return altpress
        @classmethod
        def oat2msltemp(self, oat, alt):
            # Convert layer temperature to mean sea level
            return oat + 0.0065 * alt - 273.15
        @classmethod
        def interpolate(self, t1, t2, alt1, alt2, alt):
            return t1 + (alt - alt1)*(t2 -t1)/(alt2 -alt1)
        @classmethod
        def fog2(self, rh):
            return (80 - rh)/20*24634
        @classmethod
        def toFloat(self, string, default = 0):
            # try to convert to float or return default
            try: 
                val = float(string)
            except ValueError:
                val = default
            return val
        @classmethod
        def rh2visibility(self, rh):
            return 60.0*exp(-2.5*(rh-15)/80.0)
    
    class Conf:
        '''
        Configuration variables
        '''
        syspath, dirsep = '','/'
        
        def __init__(self):
            # Inits conf
            self.syspath      = XPLMGetSystemPath(self.syspath)[:-1]
            self.respath      = self.dirsep.join([self.syspath, 'Resources', 'plugins', 'PythonScripts', 'noaaWeatherResources'])
            self.settingsfile = self.respath + self.dirsep + 'settings.pkl'
            
            self.cachepath    = self.dirsep.join([self.respath, 'cache'])
            if not os.path.exists(self.cachepath):
                os.makedirs(self.cachepath)
            
            
            # Storable settings, Defaults
            self.enabled        = True
            self.set_wind       = True
            self.set_clouds     = True
            self.set_temp       = True
            self.set_visibility = False
            self.transalt       = 32808.399000000005
            self.use_metar      = False
            self.lastgrib       = False
            self.lastwafsgrib   = False
            self.updaterate     = 4
            self.parserate      = 0.05
            self.vatsim         = False
            
            self.load()
            
            if self.lastgrib and not os.path.exists(self.cachepath + self.dirsep + self.lastgrib):
                self.lastgrib = False
            
            # Selects the apropiate wgrib binary
            platform = sys.platform
            
            if platform == 'darwin':
                sysname, nodename, release, version, machine = os.uname()
                if float(release[0:4]) > 10.6:
                    wgbin = 'OSX106wgrib2'
                else:
                    wgbin = 'OSX106wgrib2'
            elif platform == 'win32':
                wgbin = 'WIN32wgrib2.exe'
            else:
                # Linux?
                wgbin = 'linux-glib2.5-i686-wgrib2'
            
            self.wgrib2bin  = self.dirsep.join([self.respath, 'bin', wgbin])
    
        def save(self):
            conf = {
                    'version'   : __VERSION__,
                    'lastgrib'  : self.lastgrib,
                    'set_temp'  : self.set_temp,
                    'set_clouds': self.set_clouds,
                    'set_wind'  : self.set_wind,
                    'transalt'  : self.transalt,
                    'use_metar' : self.use_metar,
                    'enabled'   : self.enabled,
                    'updaterate': self.updaterate,
                    'vatsim'    : self.vatsim,
                    'lastwafsgrib' : self.lastwafsgrib
                    }
            
            f = open(self.settingsfile, 'w')
            cPickle.dump(conf, f)
            f.close()
        
        def load(self):
            if os.path.exists(self.settingsfile):
                f = open(self.settingsfile, 'r')
                try:
                    conf = cPickle.load(f)
                    f.close()
                except:
                    # Corrupted settings, remove file
                    os.remove(self.settingsfile)
                    return
                
                # may be "dangerous"
                for var in conf:
                    if var in self.__dict__:
                        self.__dict__[var] = conf[var]
            
            
    class Weather:
        '''
        Sets x-plane weather from GSF parsed data
        '''
        alt = 0.0
        def __init__(self, conf):
            
            self.conf = conf
            
            '''
            Bind datarefs
            '''
            self.winds = []
            self.clouds = []
            self.turbulence = {}
            
            for i in range(3):
                self.winds.append({
                              'alt':  EasyDref('"sim/weather/wind_altitude_msl_m[%d]"' % (i), 'float'),
                              'hdg':  EasyDref('"sim/weather/wind_direction_degt[%d]"' % (i), 'float'),
                              'speed': EasyDref('"sim/weather/wind_speed_kt[%d]"' % (i), 'float'),
                              'turbulence': EasyDref('"sim/weather/turbulence[%d]"' % (i), 'float'),
                })
                
            for i in range(3):
                self.clouds.append({
                                'top':      EasyDref('"sim/weather/cloud_tops_msl_m[%d]"' % (i), 'float'),
                                'bottom':   EasyDref('"sim/weather/cloud_base_msl_m[%d]"' % (i), 'float'),
                                'coverage': EasyDref('"sim/weather/cloud_type[%d]"' % (i), 'int'),
                                    })
                
            self.windata = []
    
            self.xpWeatherOn = EasyDref('sim/weather/use_real_weather_bool', 'int')
            self.msltemp     = EasyDref('sim/weather/temperature_sealevel_c', 'float')
            self.dewpoint    = EasyDref('sim/weather/dewpoi_sealevel_c', 'float')
            self.thermalAlt  = EasyDref('sim/weather/thermal_altitude_msl_m', 'float')
            self.visibility  = EasyDref('sim/weather/visibility_reported_m', 'float')
        
        def setWindLayer(self, xpwind, data):
            # Check current values
            if (xpwind['alt'].value, xpwind['hdg'].value, xpwind['speed'].value) != (data[0], data[1], data[2]):
                xpwind['alt'].value, xpwind['hdg'].value, xpwind['speed'].value = data[0], data[1], data[2]
        
        def setTurbulence(self, turbulence):
            '''
            Set turbulence for all wind layers with our own interpolation
            '''
            prevlayer = False
            if len(turbulence) > 1:
                for clayer in turbulence:
                    if clayer[0] > self.alt:
                        #last layer
                        break
                    else:
                        prevlayer = clayer
                if prevlayer:
                    turb = c.interpolate(prevlayer[1], clayer[1], prevlayer[0], clayer[0], self.alt)
                else:
                    turb = clayer[1]
                    
            # set turbulence
            self.winds[0]['turbulence'].value = turb
            self.winds[1]['turbulence'].value = turb
            self.winds[2]['turbulence'].value = turb
        
        def setWinds(self, winds):
            # Sets wind layers and temperature
            prevlayer = False
            wl = self.winds
            if len(winds) > 1:
                for wind in range(len(winds)):
                    wlayer = winds[wind]
                    if wlayer[0] > self.alt:
                        #last layer
                        break
                    else:
                        prevlayer = wlayer
                if prevlayer:
                    self.setWindLayer(wl[1], prevlayer)
                    self.setWindLayer(wl[2], wlayer)
                else:
                    self.setWindLayer(wl[1], wlayer)

                # Set temperature
                if self.conf.set_temp and wlayer[3]['temp']:
                    # Interpolate with previous layer
                    if prevlayer and prevlayer[0] != wlayer[0] and wlayer[3]['temp']:
                        temp = c.interpolate(prevlayer[3]['temp'], wlayer[3]['temp'], prevlayer[0], wlayer[0], self.alt)
                        self.msltemp.value = temp
                    else:
                        self.msltemp.value = wlayer[3]['temp']
                '''
                # Ser visibility
                if self.conf.set_visibility and wlayer[3]['vis']:
                    if prevlayer and prevlayer[0] != wlayer[0] and wlayer[3]['vis']:
                        self.visibility.value = c.interpolate(prevlayer[3]['vis'], wlayer[3]['vis'], prevlayer[0], wlayer[0], self.alt)
                    else:
                        self.visibility.value = wlayer[3]['vis']
                '''
                # First wind level
                if self.conf.vatsim:
                    return
                
                if not self.conf.use_metar:
                    # Set first wind level if we don't use metar
                    self.setWindLayer(wl[0], winds[0])
                elif self.alt > winds[0][0]:
                    # Set first wind level on "descent"
                    self.setWindLayer(wl[0], winds[0])
        
        def setClouds(self, clouds):
            if len(clouds) > 2:
                for i in range(3):
                    clayer  = clouds.pop()
                    cl = self.clouds
                    if clayer[2] == '0':
                        cl[i]['coverage'].value = clayer[2]
                    else:
                        if int(cl[i]['bottom'].value) != int(clayer[0]) and cl[i]['coverage'].value != clayer[2]:
                            cl[i]['bottom'].value, cl[i]['top'].value, cl[i]['coverage'].value  = clayer
        
        @classmethod
        def cc2xp(self, cover):
            #Cloud cover to X-plane
            xp = cover/100.0*4
            if xp < 1 and cover > 0:
                xp = 1
            elif cover > 89:
                xp = 4
            return xp
    
    class GFS(threading.Thread):
        '''
        NOAA GFS download and parse functions.
        '''
        cycles = [0, 6, 12, 18]
        baseurl = 'http://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_hd.pl?'
        
        params = [
                  'leftlon=0',
                  'rightlon=360',
                  'toplat=90',
                  'bottomlat=-90',
                  ]
        levels  = [
                  '700_mb', # FL100
                  '500_mb', # FL180
                  '400_mb', # FL235
                  '300_mb', # FL300
                  '200_mb', # FL380
                  'high_cloud_bottom_level',
                  'high_cloud_layer',
                  'high_cloud_top_level',
                  'low_cloud_bottom_level',
                  'low_cloud_layer',
                  'low_cloud_top_level',
                  #'mean_sea_level',
                  'middle_cloud_bottom_level',
                  'middle_cloud_layer',
                  'middle_cloud_top_level',
                  #'surface',
                   ]
        variables = ['PRES',
                     'TCDC',
                     'UGRD',
                     'VGRD',
                     'TMP',
                     'RH',
                     ]
        nwinds, nclouds = 0, 0
        
        downloading = False
        downloadWait = 0
        # wait n seconds to start download
        lastgrib    = False
        
        lat, lon, lastlat, lastlon = False, False, False, False
        
        cycle = ''
        lastcycle = ''
        
        winds  = False
        clouds = False
        newGrib = False
        
        die = threading.Event()
        
        def __init__(self, conf):
            self.conf = conf
            self.lastgrib = self.conf.lastgrib
            self.refreshStatus = False
            self.wafs = WAFS(conf)
            
            threading.Thread.__init__(self)
        
        def run(self):
            # Worker thread
            while not self.die.isSet():
                if not self.conf.enabled:
                    #Sleep if disabled
                    sleep(self.conf.parserate * 2)
                    continue
                
                # Parse grib if required
                lat, lon = int(self.lat*10/5)*5, int(self.lon*10/5)*5
                #if self.newGrib or (self.lastgrib and lat != self.lastlat and lon != self.lastlon):
                if self.lastgrib and lat != self.lastlat and lon != self.lastlon:
                    #print "XPGFS: parsing - %s - %i,%i" % (self.lastgrib, lat, lon)
                    self.parseGribData(self.lastgrib, self.lat, self.lon)
                    self.lastlat, self.lastlon = lat, lon
                    self.newGrib = False
                    self.refreshStatus = True
                
                datecycle, cycle, forecast = self.getCycleDate()
    
                if self.downloadWait < 1:
                    self.downloadCycle(datecycle, cycle, forecast)
                elif self.downloadWait > 0:
                    self.downloadWait -= self.conf.parserate
                
                # Run WAFS
                self.wafs.run(self.lat, self.lon)
                
                #wait
                if self.die.isSet():
                    return
                sleep(self.conf.parserate)
            
        def getCycleDate(self):
            '''
            Returns last cycle date avaliable
            '''
            now = datetime.utcnow() 
            #cycle is published with 3 hours delay
            cnow = now - timedelta(hours=3, minutes=0)
            #get last cycle
            for cycle in self.cycles:
                if cnow.hour >= cycle:
                    lcycle = cycle
            # Forecast
            adjs = 0
            if cnow.day != now.day:
                adjs = +24
            forecast = (adjs + now.hour - lcycle)/3*3
    
            return ( '%d%02d%02d%02d' % (cnow.year, cnow.month, cnow.day, lcycle), lcycle, forecast)
    
        def downloadCycle(self, datecycle, cycle, forecast):
            '''
            Downloads the requested grib file
            '''
            
            filename = 'gfs.t%02dz.mastergrb2f%02d' % (cycle, forecast)
            
            path = self.conf.dirsep.join([self.conf.cachepath, datecycle]) 
            cachefile = datecycle + self.conf.dirsep + filename  + '.grib'
            
            if cachefile == self.lastgrib:
                # No need to download
                return
            
            if not os.path.exists(path):
                os.makedirs(path)
            
            if self.downloading == True:
                if not self.download.q.empty():
                    #Finished downloading
                    self.lastgrib = self.download.q.get()
                    # Dowload success
                    if self.lastgrib:
                        self.conf.lastgrib = self.lastgrib
                        self.newGrib = True
                        #print "new grib file: " + self.lastgrib
                    self.downloading = False
            else:
                # Download new grib
                
                ## Build download url
                params = self.params;
                dir =  'dir=%%2Fgfs.%s%%2Fmaster' % (datecycle)
                params.append(dir)
                params.append('file=' + filename)  
                
                # add variables
                for level in self.levels:
                    params.append('lev_' + level + '=on')
                for var in self.variables:
                    params.append('var_' + var + '=on')
                
                url = self.baseurl + '&'.join(params) 
                
                #print 'XPGFS: downloading %s' % (filename)
                self.downloading = True
                self.download = AsyncDownload(self.conf, url, cachefile)
                
            return False
        
        def parseGribData(self, filepath, lat, lon):
            '''
            Executes wgrib2 and parses its output
            '''
            args = ['-s',
                    '-lon',
                    '%f' % (lon),
                    '%f' % (lat),
                    self.conf.cachepath + self.conf.dirsep + filepath
                    ]
            
            p = subprocess.Popen([self.conf.wgrib2bin] + args, stdout=subprocess.PIPE)
            it = iter(p.stdout)
            data = {}
            clouds = {}
            for line in it:
                r = line[:-1].split(':')
                # Level, variable, value
                level, variable, value = [r[4].split(' '),  r[3],  r[7].split(',')[2].split('=')[1]]
                
                if len(level) > 1:
                    if level[1] == 'cloud':
                        #cloud layer
                        clouds.setdefault(level[0], {})
                        if len(level) > 3 and variable == 'PRES':
                            clouds[level[0]][level[2]] = value
                        else:
                            #level coverage/temperature
                            clouds[level[0]][variable] = value
                    elif level[1] == 'mb':
                        # wind levels
                        data.setdefault(level[0], {})
                        data[level[0]][variable] = value
                elif level[0] == 'surface':
                    #surface layer
                    pass
    
            windlevels = []
            cloudlevels = []
            
            # Let data ready to push on datarefs.
            
            # Convert wind levels
            for level in data:
                wind = data[level]
                if 'UGRD' in wind and 'VGRD' in wind:
                    hdg, vel = c.c2p(float(wind['UGRD']), float(wind['VGRD']))
                    #print wind['UGRD'], wind['VGRD'], float(wind['UGRD']), float(wind['VGRD']), hdg, vel
                    alt = c.mb2alt(float(level))
                    
                    # Optional varialbes
                    temp, vis = False, False
                    # Temperature
                    if 'TMP' in wind:
                        temp = c.oat2msltemp(float(wind['TMP']), alt)
                    # Relative Humidity
                    if 'RH' in wind:
                        vis = c.rh2visibility(float(wind['RH']))*1000
                        if vis > 40000:
                            vis = 40000
                    else:
                        temp = False
                    windlevels.append((alt, hdg, c.ms2knots(vel), {'temp': temp, 'vis': vis}))
                    #print 'alt: %i rh: %i vis: %i' % (alt, float(wind['RH']), vis) 
            
            # Convert cloud level
            for level in clouds:
                level = clouds[level]
                if 'top' in level and 'bottom' in level and 'TCDC' in level:
                    top, bottom, cover = float(level['top']), float(level['bottom']), float(level['TCDC'])
                    #print "XPGFS: top: %.0fmbar %.0fm, bottom: %.0fmbar %.0fm %d%%" % (top * 0.01, c.mb2alt(top * 0.01), bottom * 0.01, c.mb2alt(bottom * 0.01), cover)
                    cloudlevels.append((c.mb2alt(bottom * 0.01) * 0.3048, c.mb2alt(top * 0.01) * 0.3048, int(Weather.cc2xp(cover))))
        
            windlevels.sort()        
            cloudlevels.sort(reverse=True)
            
            del data
            del clouds
            
            self.winds  = windlevels
            self.clouds = cloudlevels
            self.nwinds = len(windlevels)
            self.nclouds = len(cloudlevels)
        
        def reparse(self):
            self.lastlat = False
            self.lastlon = False
            self.newGrib = True

    class WAFS:
        '''
        World Area Forecast System - Upper Air Forecast
        Download and parse functions
        '''
        cycles    = [0, 6, 12, 18]
        forecasts = [6, 9, 12, 15, 18, 21, 24] 
        baseurl = 'http://www.ftp.ncep.noaa.gov/data/nccf/com/gfs/prod'
        
        current_datecycle   = False
        downloading     = False
        
        lastgrib = False
        lastlat, lastlon = False, False
        turbulence = {}
        nturbulence = 0
            
        def __init__(self, conf):
            self.conf        = conf
            self.downloading = False
            
            # Use last grib stored in config if still avaliable
            if self.conf.lastwafsgrib and os.path.exists(self.conf.cachepath + '/' + self.conf.lastwafsgrib):
                self.lastgrib = self.conf.lastwafsgrib
                self.current_datecycle = self.conf.lastwafsgrib.split('/')[0]
            
        def run(self, lat, lon):
            # Worker thread
            
            # Parse grib if required
            lat, lon = int(lat), int(lon)
            if self.lastgrib and lat != self.lastlat and lon != self.lastlon:
                self.parseGribData(self.lastgrib, lat, lon)
                self.lastlat, self.lastlon = lat, lon
                self.newGrib = False
            
            datecycle, cycle, forecast = self.getCycleDate()
            
            # Use new grib if dowloaded
            if self.downloading == True:
                if not self.download.q.empty():
                    lastgrib = self.download.q.get()
                    #print "Downloaded grib: " + lastgrib
                    self.downloading = False
                    if lastgrib:
                        self.lastgrib = lastgrib
                        self.conf.lastwafsgrib = lastgrib
                        self.current_datecycle = self.conf.lastwafsgrib.split('/')[0]
            
            # Download new grib if required
            if self.current_datecycle != datecycle and not self.downloading:
                self.downloadCycle(datecycle, cycle, forecast)
                
        def getCycleDate(self):
            '''
            Returns last cycle date avaliable
            '''
            now = datetime.utcnow() 
            # cycle is published with 3 hours delay
            cnow = now - timedelta(hours=6, minutes=0)
            # Get last cycle
            for cycle in self.cycles:
                if cnow.hour >= cycle:
                    lcycle = cycle
            # Forecast
            adjs = 0
            if cnow.day != now.day:
                adjs = +24
            # Elapsed from cycle
            forecast = (adjs + now.hour - lcycle)
            # Get current forecast
            for fcast in self.forecasts:
                if forecast <= fcast:
                    forecast = fcast
                    break
    
            return ( '%d%02d%02d%02d' % (cnow.year, cnow.month, cnow.day, lcycle), lcycle, forecast)
    
        def parseGribData(self, filepath, lat, lon):
            '''
            Executes wgrib2 and parses its output
            '''
            args = ['-s',
                    '-lon',
                    '%f' % (lon),
                    '%f' % (lat),
                    self.conf.cachepath + self.conf.dirsep + filepath
                    ]
            
            p = subprocess.Popen([self.conf.wgrib2bin] + args, stdout=subprocess.PIPE)
            it = iter(p.stdout)
            
            cat = {}
            for line in it:
                r = line[:-1].split(':')
                # Level, variable, value
                level, variable, value = [r[4].split(' '),  r[3],  r[7].split(',')[2].split('=')[1]]
                if len(level) > 1 and level[1] == 'mb':
                    #print level[1], variable, value
                    alt = c.mb2alt(float(level[0]))
                    value = float(value)
                    if value < 0:
                        value = 0
                    if variable == 'CTP':
                        cat[alt] = value
                    if variable == 'CAT':
                        cat[alt] = value
            
            turbulence = []
            for key, value in cat.iteritems():
                turbulence.append([key, value/10])
            
            turbulence.sort()
            
            self.turbulence = turbulence
            self.nturbulence = len(turbulence)
            
            #print cat
            #print self.turbulence
    
        def downloadCycle(self, datecycle, cycle, forecast):
            self.downloading = True
            file = "WAFS_blended_%sf%02d.grib2" % (datecycle, forecast )
            url =  "%s/gfs.%s/%s" % (self.baseurl, datecycle, file)
            cachefile = self.conf.dirsep.join([datecycle, file]) 
            #print cachefile, url
            self.download = AsyncDownload(self.conf, url, cachefile)
            
    class PythonInterface:
        '''
        Xplane plugin
        '''
        def XPluginStart(self):
            self.Name = "noaWeather - " + __VERSION__
            self.Sig = "noaWeather.joanpc.PI"
            self.Desc = "NOA GFS in x-plane"
             
            self.latdr  = EasyDref('sim/flightmodel/position/latitude', 'double')
            self.londr  = EasyDref('sim/flightmodel/position/longitude', 'double')
            self.altdr  = EasyDref('sim/flightmodel/position/elevation', 'double')
            
            self.conf = Conf()
            self.weather = Weather(self.conf)
            
            self.gfs = False
             
            # floop
            self.floop = self.floopCallback
            XPLMRegisterFlightLoopCallback(self, self.floop, -1, 0)
            
            # Menu / About
            self.Mmenu = self.mainMenuCB
            self.aboutWindow = False
            self.mPluginItem = XPLMAppendMenuItem(XPLMFindPluginsMenu(), 'XP NOAA Weather', 0, 1)
            self.mMain       = XPLMCreateMenu(self, 'XP NOAA Weather', XPLMFindPluginsMenu(), self.mPluginItem, self.Mmenu, 0)
            
            # Menu Items
            self.mReFuel    =  XPLMAppendMenuItem(self.mMain, 'Configuration', 1, 1)
            
            return self.Name, self.Sig, self.Desc
        
        def mainMenuCB(self, menuRef, menuItem):
            '''
            Main menu Callback
            '''
            if menuItem == 1:
                if (not self.aboutWindow):
                    self.CreateAboutWindow(221, 640)
                    self.aboutWindow = True
                elif (not XPIsWidgetVisible(self.aboutWindowWidget)):
                    XPShowWidget(self.aboutWindowWidget)
    
        def CreateAboutWindow(self, x, y):
            x2 = x + 450
            y2 = y - 85 - 20 * 10
            Buffer = "X-Plane NOAA GFS Weather - %s" % (__VERSION__)
            top = y
                
            # Create the Main Widget window
            self.aboutWindowWidget = XPCreateWidget(x, y, x2, y2, 1, Buffer, 1,0 , xpWidgetClass_MainWindow)
            window = self.aboutWindowWidget
            
            ## MAIN CONFIGURATION ##
            
            # Config Sub Window, style
            subw = XPCreateWidget(x+10, y-30, x+180 + 10, y2+40 -25, 1, "" ,  0,window, xpWidgetClass_SubWindow)
            XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
            x += 25
            
            # Main enalbe
            XPCreateWidget(x, y-40, x+20, y-60, 1, 'Enable XPGFS', 0, window, xpWidgetClass_Caption)
            self.enableCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonState, self.conf.enabled)
            y -= 25
    
            # Winds enalbe
            XPCreateWidget(x+5, y-40, x+20, y-60, 1, 'Wind levels', 0, window, xpWidgetClass_Caption)
            self.windsCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonState, self.conf.set_wind)
            y -= 20
            
            # Clouds enalbe
            XPCreateWidget(x+5, y-40, x+20, y-60, 1, 'Cloud levels', 0, window, xpWidgetClass_Caption)
            self.cloudsCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, self.conf.set_clouds)
            y -= 20
    
            # Temperature enalbe
            XPCreateWidget(x+5, y-40, x+20, y-60, 1, 'Temperature', 0, window, xpWidgetClass_Caption)
            self.tempCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonState, self.conf.set_temp)
            y -= 20
            
            # Turbulences enable
            XPCreateWidget(x+5, y-40, x+20, y-60, 1, 'Turbulences', 0, window, xpWidgetClass_Caption)
            self.turbCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonState, self.conf.set_temp)
            y -= 30
            x -=5
            
            # VATSIM Compatible
            XPCreateWidget(x, y-40, x+20, y-60, 1, 'VATSIM compat', 0, window, xpWidgetClass_Caption)
            self.vatsimCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.vatsimCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.vatsimCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.vatsimCheck, xpProperty_ButtonState, self.conf.vatsim)
            y -= 20
            
            # trans altitude
            XPCreateWidget(x, y-40, x+80, y-60, 1, 'Switch to METAR', 0, window, xpWidgetClass_Caption)
            self.metarCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonState, self.conf.use_metar)
            
            y -= 20
            XPCreateWidget(x+20, y-40, x+80, y-60, 1, 'Below FL', 0, window, xpWidgetClass_Caption)
            self.transAltInput = XPCreateWidget(x+100, y-40, x+140, y-62, 1, '%i' % (self.conf.transalt*3.2808399/100), 0, window, xpWidgetClass_TextField)
            XPSetWidgetProperty(self.transAltInput, xpProperty_TextFieldType, xpTextEntryField)
            XPSetWidgetProperty(self.transAltInput, xpProperty_Enabled, 1)
            
            y -= 30
            #XPCreateWidget(x, y-40, x+80, y-60, 1, 'update every #s', 0, window, xpWidgetClass_Caption)
            #self.updateRateInput = XPCreateWidget(x+100, y-40, x+140, y-62, 1, '%i' % (self.conf.updaterate), 0, window, xpWidgetClass_TextField)
            #XPSetWidgetProperty(self.updateRateInput, xpProperty_TextFieldType, xpTextEntryField)
            #XPSetWidgetProperty(self.updateRateInput, xpProperty_Enabled, 0)
            y -= 14
            #XPCreateWidget(x, y-40, x+80, y-60, 1, 'Increase to improve framerate', 0, window, xpWidgetClass_Caption)
            
            y -= 5
            # Save
            self.saveButton = XPCreateWidget(x+25, y-20, x+125, y-60, 1, "Apply & Save", 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.saveButton, xpProperty_ButtonType, xpPushButton)
            
            x += 170
            y = top
            
            # ABOUT/ STATUS Sub Window
            subw = XPCreateWidget(x+10, y-30, x2-20 + 10, y-140 -25, 1, "" ,  0,window, xpWidgetClass_SubWindow)
            # Set the style to sub window
            XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
            x += 20
            y -= 20
            
            # Add Close Box decorations to the Main Widget
            XPSetWidgetProperty(window, xpProperty_MainWindowHasCloseBoxes, 1)
            
            # Create status captions
            self.statusBuff = []
            for i in range(10):
                y -= 15
                self.statusBuff.append(XPCreateWidget(x, y, x+40, y-20, 1, '', 0, window, xpWidgetClass_Caption))
                
            self.updateStatus()
            
            y = top - 15 * 12 
            
            subw = XPCreateWidget(x-10, y, x2-20 + 10, y2 +15, 1, "" ,  0,window, xpWidgetClass_SubWindow)
            x += 30
            # Set the style to sub window
            XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
            sysinfo = [
            'X-Plane NOAA Weather: %s' % __VERSION__,
            '(c) joan perez cauhe 2012',
            ]
            for label in sysinfo:
                y -= 10
                XPCreateWidget(x, y, x+40, y-20, 1, label, 0, window, xpWidgetClass_Caption)
            
            y -= 25
            # Visit site Button
            self.aboutVisit = XPCreateWidget(x+20, y, x+120, y-20, 1, "Visit site", 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.aboutVisit, xpProperty_ButtonType, xpPushButton)
            y -= 20
            
            # Donate Button
            self.donate = XPCreateWidget(x+20, y, x+120, y-20, 1, "Donate", 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.aboutVisit, xpProperty_ButtonType, xpPushButton)
                
            # Register our widget handler
            self.aboutWindowHandlerCB = self.aboutWindowHandler
            XPAddWidgetCallback(self, window, self.aboutWindowHandlerCB)
            
            self.aboutWindow = window
        
        def aboutWindowHandler(self, inMessage, inWidget, inParam1, inParam2):
            # About window events
            if (inMessage == xpMessage_CloseButtonPushed):
                if self.aboutWindow:
                    XPDestroyWidget(self, self.aboutWindowWidget, 1)
                    self.aboutWindow = False
                return 1
    
            # Handle any button pushes
            if (inMessage == xpMsg_PushButtonPressed):
    
                if (inParam1 == self.aboutVisit):
                    from webbrowser import open_new
                    open_new('http://forums.x-plane.org/index.php?app=downloads&showfile=15453');
                    return 1
                if (inParam1 == self.donate):
                    from webbrowser import open_new
                    open_new('https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=ZQL6V9YLKRFEJ&lc=US&item_name=joan%20x%2dplane%20developer&item_number=XP%20NOAA%20Weather&currency_code=EUR&bn=PP%2dDonationsBF%3abtn_donateCC_LG%2egif%3aNonHosted');
                    return 1
                elif inParam1 == self.saveButton:
                    # Save configuration
                    self.conf.enabled       = XPGetWidgetProperty(self.enableCheck, xpProperty_ButtonState, None)
                    self.conf.set_wind      = XPGetWidgetProperty(self.windsCheck, xpProperty_ButtonState, None)
                    self.conf.set_clouds    = XPGetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, None)
                    self.conf.set_temp      = XPGetWidgetProperty(self.tempCheck, xpProperty_ButtonState, None)
                    self.conf.use_metar     = XPGetWidgetProperty(self.metarCheck, xpProperty_ButtonState, None)
                    self.conf.vatsim        = XPGetWidgetProperty(self.vatsimCheck, xpProperty_ButtonState, None)
                    
                    buff = []
                    XPGetWidgetDescriptor(self.transAltInput, buff, 256)
                    self.conf.transalt = c.toFloat(buff[0], 100) * 0.3048 * 100
                    #buff = []
                    #XPGetWidgetDescriptor(self.updateRateInput, buff, 256)
                    #self.conf.updaterate = c.toFloat(buff[0], 1)
                    
                    if self.conf.vatsim: 
                        self.conf.set_clouds = False
                        self.conf.set_temp   = False
                        self.conf.use_metar  = False
                        self.weather.winds[0]['alt'].value = self.conf.transalt
                    
                    if not self.conf.use_metar:
                        self.weather.xpWeatherOn.value = 0
                    else:
                        self.weather.winds[0]['alt'].value = self.conf.transalt   
                    
                    self.conf.save()
                    self.aboutWindowUpdate()
            return 0
        
        def aboutWindowUpdate(self):
            XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonState, self.conf.enabled)
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonState, self.conf.set_wind)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, self.conf.set_clouds)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonState, self.conf.set_temp)
            XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonState, self.conf.use_metar)
            XPSetWidgetProperty(self.vatsimCheck, xpProperty_ButtonState, self.conf.vatsim)
            self.updateStatus()
            
        def updateStatus(self):
            '''
            Update Status window
            '''
            sysinfo = []
            if self.gfs:
                
                if self.gfs.lastgrib:
                    lastgrib = self.gfs.lastgrib.split('/')
                    lastwafsgrib = self.conf.lastwafsgrib.split('/')
                    sysinfo = [
                    'XPGFS Status:',
                    'lat: %.2f, lon: %.2f' % (self.gfs.lat, self.gfs.lon),
                    'GFS Cycle: %s' % (lastgrib[0]),
                    'WAFS Cycle: %s' % (lastwafsgrib[0]),
                    'wind layers: %i' % (self.gfs.nwinds),
                    'cloud layers: %i' % (self.gfs.nclouds),
                    'turbulence layers: %i' % (self.gfs.wafs.nturbulence),
                    ]
                if self.gfs.downloading:
                    sysinfo.append('Downloading new cycle.')
            else:
                sysinfo = ['XPGFS Status:',
                           'Data not ready'
                           ]
            i = 0
            for label in sysinfo:
                XPSetWidgetDescriptor(self.statusBuff[i], label)
                i +=1
    
        def floopCallback(self, elapsedMe, elapsedSim, counter, refcon):
            '''
            Floop Callback
            '''
            
            if not self.conf.enabled or not self.gfs:
                # Worker stoped wait 4s
                return 4
            
            # get acf position
            self.gfs.lat = self.latdr.value
            self.gfs.lon = self.londr.value
            self.weather.alt = self.altdr.value
            
            # Switch METAR/GFS mode
            if self.conf.use_metar:
                if self.weather.xpWeatherOn.value == 1:
                    if self.weather.alt > self.conf.transalt:
                        self.weather.xpWeatherOn.value = 0
                    else:
                        return -1
                else:
                    if self.weather.alt < self.conf.transalt:
                        self.weather.winds[0]['alt'].value = self.conf.transalt 
                        self.weather.xpWeatherOn.value = 1
                        return -1

            # Set winds and clouds
            if self.conf.set_wind and self.gfs.winds:
                self.weather.setWinds(self.gfs.winds)
            if self.conf.set_clouds and self.gfs.clouds:
                self.weather.setClouds(self.gfs.clouds)
            
            # Set turbulence
            if self.gfs.wafs.turbulence:
                self.weather.setTurbulence(self.gfs.wafs.turbulence)
                
            #if self.aboutWindow and self.gfs.refreshStatus:
            if self.aboutWindow and XPIsWidgetVisible(self.aboutWindowWidget):
                self.updateStatus()
                self.gfs.refreshStatus = False
            return -1
        
        def XPluginStop(self):
            # Destroy windows
            if self.aboutWindow:
                XPDestroyWidget(self, self.aboutWindowWidget, 1)
            XPLMUnregisterFlightLoopCallback(self, self.floop, 0)
            
            # kill working thread
            if self.gfs:
                self.gfs.die.set()
            # Destroy menus
            XPLMDestroyMenu(self, self.mMain)
            self.conf.save()
            
        def XPluginEnable(self):
            return 1
        
        def XPluginDisable(self):
            pass
        
        def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
            if (inParam == XPLM_PLUGIN_XPLANE and inMessage == XPLM_MSG_AIRPORT_LOADED):
                # X-Plane loaded, start worker
                if not self.gfs:
                    self.gfs = GFS(self.conf)
                    self.gfs.start()
                