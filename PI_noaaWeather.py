'''
X-plane NOAA GFS weather plugin.

Sets x-plane wind and cloud layers using NOAA real/forecast data.
This plugin downloads required data from NOAA servers.

Uses wgrib2 to parse NOAA grib2 data files.
Includes wgrib2 binaries for Mac Win32 and linux i386glibc6
Win32 wgrib2 requires cgywin

This plugin is under developement and INCOMPLETE

TODO:
- Remove shear and turbulence on transition
- Turbulences, rain, snow, wind shears, visibility
- msl pressure
- clear cache
- Store only last grib file?

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
import cPickle

__VERSION__ = 'beta 5.1'

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
    def toFloat(self, string):
        # try to convert to float or return 100
        try: 
            val = float(string)
        except ValueError:
            val = 100
        return val

class Conf:
    '''
    Configuration variables
    '''
    syspath, dirsep = '','/'
    
    def __init__(self):
        # Inits conf
        self.syspath    = XPLMGetSystemPath(self.syspath)[:-1]
        self.respath    = self.dirsep.join([self.syspath, 'Resources', 'plugins', 'PythonScripts', 'noaaWeatherResources'])
        self.settingsfile = self.respath + self.dirsep + 'settings.pkl'
        
        self.cachepath  = self.dirsep.join([self.respath, 'cache'])
        if not os.path.exists(self.cachepath):
            os.makedirs(self.cachepath)
        
        
        # Storable settings, Defaults
        self.enabled    = True
        self.set_wind   = True
        self.set_clouds = True
        self.set_temp   = True
        self.transalt   = 32808.399000000005
        self.use_metar  = False
        self.lastgrib   = False
        
        self.load()
        
        if self.lastgrib and not os.path.exists(self.cachepath + self.dirsep + self.lastgrib):
            self.lastgrib = False
        
        self.dirsep = os.sep
        
        # Selects the apropiate wgrib binari
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
                }
        
        f = open(self.settingsfile, 'w')
        cPickle.dump(conf, f)
        f.close()
    
    def load(self):
        if os.path.exists(self.settingsfile):
            f = open(self.settingsfile, 'r')
            conf = cPickle.load(f)
            f.close()
            
            # may be "dangerous"
            for var in conf:
                if var in self.__dict__:
                    self.__dict__[var] = conf[var]
        
        
class weather:
    '''
    Sets x-plane weather
    '''
    alt = 0.0
    def __init__(self, conf):
        
        self.conf = conf
        
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

        self.xpWeatherOn = EasyDref('sim/weather/use_real_weather_bool', 'int')
        self.msltemp     = EasyDref('sim/weather/temperature_sealevel_c', 'float')
        self.dewpoint    = EasyDref('sim/weather/dewpoi_sealevel_c', 'float')
        self.thermalAlt  = EasyDref('sim/weather/thermal_altitude_msl_m', 'float')
        
    def setWinds1(self, winds):
        wl = self.winds
        if len(winds) > 2:
            i = 1
            for wind in range(len(winds)):
                if i > 3:
                    return
                wlayer  = winds[wind]
                if wlayer[0] > self.alt:
                    wl[i]['alt'].value, wl[i]['hdg'].value, wl[i]['speed'].value  = wlayer
                i += 1
    
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
                wl[1]['alt'].value, wl[1]['hdg'].value, wl[1]['speed'].value  = prevlayer[0], prevlayer[1], prevlayer[2]
                wl[2]['alt'].value, wl[2]['hdg'].value, wl[2]['speed'].value  = wlayer[0], wlayer[1], wlayer[2]
            else:
                wl[1]['alt'].value, wl[1]['hdg'].value, wl[1]['speed'].value  = wlayer[0], wlayer[1], wlayer[2]
            # Set temperature
            if wlayer[3]:
                if prevlayer and prevlayer[0] != wlayer[0] and prevlayer[3]:
                    self.msltemp.value = c.interpolate(prevlayer[3], wlayer[3], prevlayer[0], wlayer[0], self.alt)
                else:
                    self.msltemp.value = wlayer[3]
            
            if not self.conf.use_metar:
                wl[0]['alt'].value, wl[0]['hdg'].value, wl[0]['speed'].value  = winds[0][0], winds[0][1], winds[0][2]
    
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
              #'200_mb', # FL380
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
                 'TMP'
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
        threading.Thread.__init__(self)
    
    def run(self):
        # Worker thread
        while not self.die.isSet():
            if not self.conf.enabled:
                #Sleep if disabled
                sleep(10)
                continue
            
            # Parse grib if required
            lat, lon = int(self.lat*10/5*5), int(self.lon*10/5*5)
            if self.newGrib or (self.lastgrib and lat != self.lastlat and lon != self.lastlon):
                print "xpNooaW: parsing - %s - %i,%i" % (self.lastgrib, lat, lon)
                self.parseGribData(self.lastgrib, self.lat, self.lon)
                self.lastlat, self.lastlon = lat, lon
                self.newGrib = False
            
            datecycle, cycle, forecast = self.getCycleDate()
            if self.downloadWait < 1:
                self.downloadCycle(datecycle, cycle, forecast)
            else:
                self.downloadWait -= 4
    
        #wait
        if self.die.isSet():
            return
        sleep(4)
    
    class asyncDownload(threading.Thread):
        '''
        Asyncronous download
        '''
        def __init__(self, parent, url, cachefile):
            threading.Thread.__init__(self)
            self.url = url
            self.cachefile = cachefile
            self.parent = parent
            self.conf = parent.conf
        def run(self):
            filepath = self.conf.cachepath + self.conf.dirsep + self.cachefile
            urlretrieve(self.url, filepath)
            
            if os.path.getsize(filepath) > 1:
                self.parent.lastgrib = self.cachefile
                self.conf.lastgrib = self.cachefile
                self.parent.newGrib = True
            else:
                # File unavaliable, empty file; wait 10 minutes
                print "XPGFS: Error downloading: %s" % (self.cachefile)
                self.parent.downloadWait = 10 * 60
                if os.path.exists(filepath):
                    os.remove(filepath)

            self.parent.downloading = False
    
    def getCycleDate(self):
        '''
        Returns last cycle date avaliable
        '''
        now = datetime.utcnow() 
        #cycle is published with 3 hours delay
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

    def downloadCycle(self, datecycle, cycle, forecast):
        '''
        Downloads the requested grib file
        '''
        
        filename = 'gfs.t%02dz.mastergrb2f%02d' % (cycle, forecast)
        
        path = self.conf.dirsep.join([self.conf.cachepath, datecycle]) 
        cachefile = datecycle + self.conf.dirsep + filename  + '.grib'
        
        if not os.path.exists(path):
            os.makedirs(path)
        
        if cachefile == self.lastgrib:
            # No need to download
            return
        
        elif self.downloading == False:
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
            
            print 'XPGFS: downloading %s' % (filename)
            self.downloading = True
            self.download = self.asyncDownload(self, url, cachefile)
            self.download.start()
            
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
                alt = c.mb2alt(float(level))
                if 'TMP' in wind:
                    temp = c.oat2msltemp(float(wind['TMP']), alt)
                else:
                    temp = False
                windlevels.append((alt, hdg, c.ms2knots(vel), temp))
        
        # Convert cloud level
        for level in clouds:
            level = clouds[level]
            if 'top' in level and 'bottom' in level and 'TCDC' in level:
                top, bottom, cover = float(level['top']), float(level['bottom']), float(level['TCDC'])
                print "XPGFS: top: %.0fmbar %.0fm, bottom: %.0fmbar %.0fm %d%%" % (top * 0.01, c.mb2alt(top * 0.01), bottom * 0.01, c.mb2alt(bottom * 0.01), cover)
                cloudlevels.append((c.mb2alt(bottom * 0.01) * 0.3048, c.mb2alt(top * 0.01) * 0.3048, int(weather.cc2xp(cover))))
    
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

class PythonInterface:
    '''
    Xplane plugin
    '''
    def XPluginStart(self):
        self.Name = "noaWeather - "
        self.Sig = "noaWeather.joanpc.PI"
        self.Desc = "NOA GFS in x-plane"
         
        self.latdr  = EasyDref('sim/flightmodel/position/latitude', 'double')
        self.londr  = EasyDref('sim/flightmodel/position/longitude', 'double')
        self.altdr  = EasyDref('sim/flightmodel/position/elevation', 'double')
        
        self.conf = Conf()
        self.weather = weather(self.conf)
        
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
        x2 = x + 430
        y2 = y - 50 - 20 * 9
        Buffer = "X-Plane NOAA GFS Weather"
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
        XPCreateWidget(x, y-40, x+20, y-60, 1, 'Enable GFS', 0, window, xpWidgetClass_Caption)
        self.enableCheck = XPCreateWidget(x+80, y-40, x+90, y-60, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonState, self.conf.enabled)
        y -= 20

        # Winds enalbe
        XPCreateWidget(x, y-40, x+20, y-60, 1, 'Set winds', 0, window, xpWidgetClass_Caption)
        self.windsCheck = XPCreateWidget(x+80, y-40, x+90, y-60, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonState, self.conf.set_wind)
        y -= 20
        
        # Clouds enalbe
        XPCreateWidget(x, y-40, x+20, y-60, 1, 'Set clouds', 0, window, xpWidgetClass_Caption)
        self.cloudsCheck = XPCreateWidget(x+80, y-40, x+90, y-60, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, self.conf.set_clouds)
        y -= 20

        # Temperature enalbe
        XPCreateWidget(x, y-40, x+20, y-60, 1, 'Set Temp', 0, window, xpWidgetClass_Caption)
        self.tempCheck = XPCreateWidget(x+80, y-40, x+90, y-60, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonState, self.conf.set_temp)
        y -= 30     
        
        # trans altitude
        XPCreateWidget(x, y-40, x+80, y-60, 1, 'Use METAR below FL', 0, window, xpWidgetClass_Caption)
        self.metarCheck = XPCreateWidget(x+115, y-44, x+145, y-60, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonState, self.conf.use_metar)
        
        y -= 20
        self.transAltInput = XPCreateWidget(x, y-40, x+140, y-62, 1, '%i' % (self.conf.transalt*3.2808399/1000), 0, window, xpWidgetClass_TextField)
        XPSetWidgetProperty(self.transAltInput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.transAltInput, xpProperty_Enabled, 1)
        
        y -= 40
        # Save
        self.saveButton = XPCreateWidget(x+20, y-20, x+120, y-60, 1, "Apply & Save", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.saveButton, xpProperty_ButtonType, xpPushButton)
        
        x += 170
        y = top
        
        
        # ABOUT/ STATUS Sub Window
        subw = XPCreateWidget(x+10, y-30, x2-20 + 10, y2+40 -25, 1, "" ,  0,window, xpWidgetClass_SubWindow)
        # Set the style to sub window
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        x += 20
        y -= 20
        
        # Add Close Box decorations to the Main Widget
        XPSetWidgetProperty(window, xpProperty_MainWindowHasCloseBoxes, 1)
        
        
        if self.gfs and self.gfs.lastgrib:
            
            lastgrib = self.gfs.lastgrib.split('/');
            sysinfo = [
            'XPGFS Status:',
            'lat: %f, lon: %f' % (self.gfs.lat, self.gfs.lat),
            'GFS Cycle: %s' % (lastgrib[0]),
            'GRIB File: %s' % (lastgrib[1]),
            'wind levels: %i' % (self.gfs.nwinds),
            'cloud levels: %i' % (self.gfs.nclouds),
            ]
        else:
            sysinfo = ['XPGFS Status:',
                       'Data not ready'
                       ]
        
        for label in sysinfo:
            y -= 15
            XPCreateWidget(x, y, x+40, y-20, 1, label, 0, window, xpWidgetClass_Caption)
        
        y = top - 15 * 9   
        sysinfo = [
        'X-Plane NOAA Weather: %s' % __VERSION__,
        '(c) joan perez cauhe 2012',
        ]
        for label in sysinfo:
            y -= 10
            XPCreateWidget(x, y, x+40, y-20, 1, label, 0, window, xpWidgetClass_Caption)
        
        # Visit site 
        self.aboutVisit = XPCreateWidget(x+20, y-20, x+120, y-60, 1, "Visit site", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.aboutVisit, xpProperty_ButtonType, xpPushButton)
            
        # Register our widget handler
        self.aboutWindowHandlerCB = self.aboutWindowHandler
        XPAddWidgetCallback(self, window, self.aboutWindowHandlerCB)
    
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
                open_new('https://github.com/joanpc/XplaneNoaaWeather');
                return 1
            elif inParam1 == self.saveButton:
                self.conf.enabled       = XPGetWidgetProperty(self.enableCheck, xpProperty_ButtonState, None)
                self.conf.set_wind      = XPGetWidgetProperty(self.windsCheck, xpProperty_ButtonState, None)
                self.conf.set_clouds    = XPGetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, None)
                self.conf.set_temp      = XPGetWidgetProperty(self.tempCheck, xpProperty_ButtonState, None)
                self.conf.use_metar     = XPGetWidgetProperty(self.metarCheck, xpProperty_ButtonState, None)
                
                buff = []
                XPGetWidgetDescriptor(self.transAltInput, buff, 256)
                self.conf.transalt = c.toFloat(buff[0]) * 0.3048 * 1000
                
                if not self.conf.use_metar:
                    self.weather.xpWeatherOn.value = 0
                else:
                    self.weather.winds[0]['alt'].value = self.conf.transalt   
                  
                self.conf.save()
        return 0

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
        
        if self.conf.use_metar:
            if self.weather.xpWeatherOn.value:
                if self.weather.alt > self.conf.transalt:
                    self.weather.xpWeatherOn.value = 0
                else:
                    return -1
            else:
                if self.weather.alt < self.conf.transalt:
                    self.weather.winds[0]['alt'].value = self.conf.transalt
                    self.weather.xpWeatherOn.value = 1
        
        # Set winds and clouds
        if self.conf.set_wind and self.gfs.winds:
            self.weather.setWinds(self.gfs.winds)
        if self.conf.set_clouds and self.gfs.clouds:
            self.weather.setClouds(self.gfs.clouds)
        
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
                