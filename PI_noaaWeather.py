'''
X-plane wind layers plugin

Sets x-plane wind and cloud layers using NOAA real/forecast data.
This plugin downloads required data from NOAA servers.

Uses wgrib2 to parse NOAA grib2 data files.
Includes wgrib2 binaries for Mac Win32 and linux i386glibc6
Win32 wgrib2 requires cgywin

This plugin is under developement and INCOMPLETE

TODO:
- Turbulences, rain, snow
- msl pressure, temperature
- Detect downloaded empty grib files
- Simple GUI (configuration, status display, clear cache)
- Store only last grib file?

Copyright (C) 2011  Joan Perez i Cauhe
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

#Python includes
from urllib import urlretrieve
from datetime import datetime, timedelta
import threading, subprocess
from math import hypot, atan2, degrees
from EasyDref import EasyDref
import os
import sys
from time import sleep


class c:
    '''
    Conversion tools
    '''
    @classmethod
    def ms2knots(self, val):
        return val * 0.514444
    
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
        return a, r
    
    @classmethod
    def mb2alt(self, mb):
        pstd = 1013.25
        altpress =  (1 - (mb/pstd)**0.190284) * 145366.45 * 0.3048 #feet2meter
        return altpress

class conf:
    '''
    Configuration variables
    '''
    syspath, dirsep = '','/'
    lastgrib = False
    
    @classmethod
    def init(self):
        # Inits conf
        
        #self.dirsep = XPLMGetDirectorySeparator(self.dirsep)
        
        # Select the apropiate wgrib binari
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
        
        self.syspath    = XPLMGetSystemPath(self.syspath)[:-1]
        self.respath    = self.dirsep.join([self.syspath, 'Resources', 'plugins', 'PythonScripts', 'noaaWeatherResources'])
        
        self.cachepath  = self.dirsep.join([self.respath, 'cache'])
        if not os.path.exists(self.cachepath):
            os.makedirs(self.cachepath)
        
        self.wgrib2bin  = self.dirsep.join([self.respath, 'bin', wgbin])
        pass
    
    pass

class weather:
    '''
    Sets x-plane weather
    '''
    def __init__(self):
        '''
        Bind datarefs
        '''
        self.winds = []
        self.clouds = []
        
        for i in range(3):
            self.winds.append({
                          'alt':  EasyDref('"sim/weather/wind_altitude_msl_m[%d]"' % (i), 'float'),
                          'hdg':  EasyDref('"sim/weather/wind_direction_degt[%d]"' % (i), 'float'),
                          'speed': EasyDref('"sim/weather/wind_speed_kt[%d]"' % (i), 'float'),
            })
            
        for i in range(3):
            self.clouds.append({
                            'top':      EasyDref('"sim/weather/cloud_tops_msl_m[%d]"' % (i), 'float'),
                            'bottom':   EasyDref('"sim/weather/cloud_base_msl_m[%d]"' % (i), 'float'),
                            'coverage': EasyDref('"sim/weather/cloud_type[%d]"' % (i), 'int'),
                                })
        self.windata = []

    def setWinds(self, winds):
        if len(winds) > 2:
            for i in range(2,-1,-1):
                wlayer  = winds.pop()
                wl = self.winds
                #print 'winds', wl[i]['alt'].value, wl[i]['hdg'].value, wl[i]['speed'].value 
                wl[i]['alt'].value, wl[i]['hdg'].value, wl[i]['speed'].value  = wlayer
    
    def setClouds(self, clouds):
        if len(clouds) > 2:
            for i in range(2,-1,-1):
                none = 0;
                clayer  = clouds.pop()
                cl = self.clouds
                #print "CLOUDS ===== ", cl[i]['bottom'].value, cl[i]['top'].value, cl[i]['coverage'].value
                
                if clayer[2] == '0':
                    #cl[i]['coverage'].value = clayer[2]
                    pass
                else:
                    #cl[i]['bottom'].value, cl[i]['top'].value, cl[i]['coverage'].value  = clayer
                    if int(cl[i]['bottom'].value) != int(clayer[0]) and cl[i]['coverage'].value != clayer[2]:
                        # Sets clouds only on changes to prevent x-plane clouds regeneration
                        cl[i]['bottom'].value  = clayer[1]
                        cl[i]['coverage'].value  = clayer[2]
                #print "CLOUDS set== ", clayer[0], clayer[0] + 100, clayer[2]
    def disableXPWeather(self):
        pass
    
    @classmethod
    def cc2xp(self, cover):
        #Cloud cover to X-plane
        xp = cover/100.0*4
        if xp < 0 and cover > 0:
            xp = 1
        return xp

class GFS(threading.Thread):
    '''
    Downloads from NOAA the latest GFS grib data
    '''
    cycles = [0, 6, 12, 18]
    baseurl = 'http://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_hd.pl?'
    
    params = [
              'lev_700_mb=on',
              'lev_450_mb=on',
              'lev_200_mb=on',
              'lev_high_cloud_bottom_level=on',
              'lev_high_cloud_layer=on',
              'lev_high_cloud_top_level=on',
              'lev_low_cloud_bottom_level=on',
              'lev_low_cloud_layer=on',
              'lev_low_cloud_top_level=on',
              'lev_mean_sea_level=on',
              'lev_middle_cloud_bottom_level=on',
              'lev_middle_cloud_layer=on',
              'lev_middle_cloud_top_level=on',
              'all_var=on',
              'leftlon=0',
              'rightlon=360',
              'toplat=90',
              'bottomlat=-90',
              ]
    downloading = False
    lastgrib    = False
    
    lat, lon, lastlat, lastlon = False, False, False, False
    
    cycle = ''
    lastcycle = ''
    
    winds  = False
    clouds = False
    newGrib = False
    
    die = threading.Event()
    
    def run(self):
        
        while not self.die.isSet():
            # working thread
            lat, lon = int(self.lat*10/5*5), int(self.lon*10/5*5)
            if self.newGrib or (self.lastgrib and lat != self.lastlat and lon != self.lastlon):
                print "xpNooaW: parsing"
                self.parseGribData(self.lastgrib, self.lat, self.lon)
                self.lastlat, self.lastlon = lat, lon
                self.newGrib = False
            
            datecycle, cycle, forecast = self.getCycleDate()
            gribfile = self.getLastCycle(datecycle, cycle, forecast)
            if gribfile:
                self.lastgrib = gribfile
        #wait
        sleep(10)
    
    class asyncDownload(threading.Thread):
        '''
        Asyncronous download
        '''
        def __init__(self, parent, url, cachefile):
            threading.Thread.__init__(self)
            self.url = url
            self.cachefile = cachefile
            self.parent = parent
        def run(self):
            urlretrieve(self.url, conf.cachepath + conf.dirsep + self.cachefile)
            self.parent.downloading = False
            self.parent.lastgrib = self.cachefile
            self.parent.newGrib = True
    
    def getCycleDate(self):
        '''
        Returns last cycle date avaliable
        '''
        now = datetime.utcnow() 
        #cycle is generated with 2:30 hours delay
        cnow = now - timedelta(hours=3, minutes=30)
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

    def getLastCycle(self, datecycle, cycle, forecast):
        params = self.params[:];
        
        dir =  'dir=%%2Fgfs.%s%%2Fmaster' % (datecycle)
        params.append(dir)
        filename = 'gfs.t%02dz.mastergrb2f%02d' % (cycle, forecast)
        params.append('file=' + filename)
    
        url = self.baseurl + '&'.join(params)
        
        path = conf.dirsep.join([conf.cachepath, datecycle]) 
        cachefile = datecycle + conf.dirsep + filename  + '.grib'
        
        if not os.path.exists(path):
            os.makedirs(path)
            
        if os.path.exists(conf.cachepath + conf.dirsep + cachefile):
            #print 'using cache file %s' % (cachefile)
            self.lastgrib = cachefile
            return cachefile
        
        elif self.downloading == False:
            print 'downloading file'
            self.downloading = True
            self.download = self.asyncDownload(self, url, cachefile)
            self.download.start()
        return False
    
    def parseGribData(self, filepath, lat, lon):
        args = ['-s',
                '-lon',
                '%f' % (lon),
                '%f' % (lat),
                conf.cachepath + conf.dirsep + filepath
                ]
        
        p = subprocess.Popen([conf.wgrib2bin] + args, stdout=subprocess.PIPE)
        it = iter(p.stdout)
        data = {}
        clouds = {}
        for line in it:
            r = line[:-1].split(':')
            # Level, variable, value
            level, variable, value = [r[4].split(' '),  r[3],  r[7].split(',')[2].split('=')[1]]
            
            if level[1] == 'mb':
                #wind level
                data.setdefault(level[0], {})
                data[level[0]][variable] = value
            elif level[1] == 'cloud':
                #cloud layer
                clouds.setdefault(level[0], {})
                
                if len(level) > 3:
                    #level margins
                    clouds[level[0]][level[2]] = value
                else:
                    #level coverage/temperature
                    clouds[level[0]][variable] = value
                
                pass

        windlevels = []
        cloudlevels = []
        
        # Let data ready to push on datarefs
        
        # Convert wind levels
        for level in data:
            wind = data[level]
            if 'UGRD' in wind and 'VGRD' in wind:
                hdg, vel = c.c2p(float(wind['UGRD']), float(wind['VGRD']))
                windlevels.append((c.mb2alt(float(level)), hdg, c.ms2knots(vel)))
        
        # Convert cloud level
        lastbottom = 40000
        for level in clouds:
            level = clouds[level]
            if 'top' in level and 'bottom' in level and 'TCDC' in level:
                top, bottom, cover = float(level['top']), float(level['bottom']), float(level['TCDC'])
                top = bottom +1
                #print "top: %.0fmbar %.0fm, bottom: %.0fmbar %.0fm" % (top * 0.01, c.mb2alt(top * 0.01), bottom * 0.01, c.mb2alt(bottom * 0.01))
                cloudlevels.append((c.mb2alt(bottom * 0.01) * 0.3048, c.mb2alt(top * 0.01) * 0.3048, int(weather.cc2xp(cover))))
                lastbottom = bottom
    
        windlevels.sort()        
        cloudlevels.sort()

        self.winds  = windlevels
        self.clouds = cloudlevels

class PythonInterface:
    '''
    Xplane plugin
    '''
    def XPluginStart(self):
        self.Name = "noaWeather - "
        self.Sig = "noaWeather.joanpc.PI"
        self.Desc = "NOA Weather in your x-plane"
         
        self.latdr  = EasyDref('sim/flightmodel/position/latitude', 'double')
        self.londr  = EasyDref('sim/flightmodel/position/longitude', 'double')
        
        conf.init()
        self.weather = weather()
        
        # Worker thread
        self.gfs = GFS()
        self.gfs.start()
        self.gfs
         
        # floop
        self.floop = self.floopCallback
        XPLMRegisterFlightLoopCallback(self, self.floop, -1, 0)
         
        return self.Name, self.Sig, self.Desc

    def floopCallback(self, elapsedMe, elapsedSim, counter, refcon):
        '''
        Floop Callback
        gets all the data and prints it to file
        '''
        
        if self.gfs.winds:
            self.weather.setWinds(self.gfs.winds)
        if self.gfs.clouds:
            self.weather.setClouds(self.gfs.clouds)
        
        # get acf position
        self.gfs.lat = self.latdr.value
        self.gfs.lon = self.londr.value
        
        #print 'lat: %f lon: %f' % (self.latdr.value, self.londr.value)
        
        return -1
    
    def XPluginStop(self):
        self.gfs.die.set()
        XPLMUnregisterFlightLoopCallback(self, self.floop, 0)
        pass
        
    def XPluginEnable(self):
        return 1
    
    def XPluginDisable(self):
        pass
    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        pass