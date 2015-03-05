'''
X-plane NOAA GFS weather plugin.

Sets x-plane wind, temperature, cloud and turbulence layers 
using NOAA real/forecast data and METAR reports.

The plugin downloads all the required data from NOAA servers.

Uses wgrib2 to parse NOAA grib2 data files.
Wgrib2 binaries for MacOSX Win32 and linux i386glibc6 are 
provided Win32 wgrib2 requires cgywin also included in the 
bin folder.

Official site:
http://x-plane.joanpc.com/plugins/xpgfs-noaa-weather

For support visit:
http://forums.x-plane.org/index.php?showtopic=72313

Github project page:
https://github.com/joanpc/XplaneNoaaWeather

Copyright (C) 2012-2015 Joan Perez i Cauhe
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

import cPickle
import socket
import threading
import subprocess
import os
from datetime import datetime

from noaweather import EasyDref, Conf, c
        
class Weather:
    '''
    Sets x-plane weather from GSF parsed data
    '''
    alt = 0.0
    ref_winds = {}
    lat, lon, last_lat, last_lon = 99, 99, False, False
    
    def __init__(self, conf):
        
        self.conf = conf
        self.lastMetarStation = False
        
        '''
        Bind datarefs
        '''
        self.winds = []
        self.clouds = []
        self.turbulence = {}
        
        for i in range(3):
            self.winds.append({
                          'alt':    EasyDref('"sim/weather/wind_altitude_msl_m[%d]"' % (i), 'float'),
                          'hdg':    EasyDref('"sim/weather/wind_direction_degt[%d]"' % (i), 'float'),
                          'speed':  EasyDref('"sim/weather/wind_speed_kt[%d]"' % (i), 'float'),
                          'gust' :  EasyDref('"sim/weather/shear_speed_kt[%d]"' % (i), 'float'),
                          'gust_hdg' : EasyDref('"sim/weather/shear_direction_degt[%d]"' % (i), 'float'),
                          'turbulence': EasyDref('"sim/weather/turbulence[%d]"' % (i), 'float'),
            })
            
        for i in range(3):
            self.clouds.append({
                            'top':      EasyDref('"sim/weather/cloud_tops_msl_m[%d]"' % (i), 'float'),
                            'bottom':   EasyDref('"sim/weather/cloud_base_msl_m[%d]"' % (i), 'float'),
                            'coverage': EasyDref('"sim/weather/cloud_type[%d]"' % (i), 'int'),
                            # XP10 'coverage': EasyDref('"sim/weather/cloud_coverage[%d]"' % (i), 'float'),
                                })
            
        self.windata = []

        self.xpWeatherOn = EasyDref('sim/weather/use_real_weather_bool', 'int')
        self.msltemp     = EasyDref('sim/weather/temperature_sealevel_c', 'float')
        self.msldewp    = EasyDref('sim/weather/dewpoi_sealevel_c', 'float')
        self.thermalAlt  = EasyDref('sim/weather/thermal_altitude_msl_m', 'float')
        self.visibility  = EasyDref('sim/weather/visibility_reported_m', 'float')
        self.pressure    = EasyDref('sim/weather/barometer_sealevel_inhg', 'float')
        
        self.precipitation = EasyDref('sim/weather/rain_percent', 'float')
        self.thunderstorm = EasyDref('sim/weather/thunderstorm_percent', 'float')
        
        self.mag_deviation = EasyDref('sim/flightmodel/position/magnetic_variation', 'float')
        
        # Data
        self.weatherData = False
        self.weatherClientThread = False
        
        self.windAlts = -1
        
        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.die = threading.Event()
        self.lock = threading.Lock()
        
        self.newData = False
        
        self.startWeatherServer()
    
    def startWeatherClient(self):
        if not self.weatherClientThread:
            self.weatherClientThread = threading.Thread(target=self.weatherClient)
            self.weatherClientThread.start()
            
    def weatherClient(self):
        '''
        Wheather client thread fetches weather from the server
        '''
        
        # Send something for windows to bind
        self.sock.sendto("?%.2f|%.2f\n" % (99, 99), ('127.0.0.1', self.conf.server_port))
        
        while True:
            received = self.sock.recv(1024*8)
            self.weatherData = cPickle.loads(received)
            if self.die.is_set() or self.weatherData == '!bye':
                break
            else:
                self.newData = True
    
    def weatherClientSend(self, msg):
        if self.weatherClientThread:
            self.sock.sendto(msg,('127.0.0.1', self.conf.server_port))
    
    def startWeatherServer(self):
        DETACHED_PROCESS = 0x00000008
        args = [self.conf.pythonpath, os.sep.join([self.conf.respath, 'weatherServer.py']), self.conf.syspath]
        
        if self.conf.spinfo:
            subprocess.Popen(args, startupinfo=self.conf.spinfo, close_fds=True, creationflags=DETACHED_PROCESS)
        else:
            subprocess.Popen(args, close_fds=True)
    
    def shutdown(self):
        # Shutdown client and server
        #self.die.set()
        self.weatherClientSend('!shutdown')
        self.weatherClientThread.join()
        self.weatherClientThread = False
     
    def setTurbulence(self, turbulence):
        '''
        Set turbulence for all wind layers with our own interpolation
        '''
        turb = 0
        
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
    
    def setWinds(self, winds, elapsed):
        '''Set winds: Interpolate layers and transition new data'''  
        
        # Append metar layer
        if 'metar' in self.weatherData and 'wind' in self.weatherData['metar']:
            alt = self.weatherData['metar']['elevation']
            hdg, speed, gust = self.weatherData['metar']['wind']
            extra = {'gust': gust, 'metar': True}
            
            alt += self.conf.metar_agl_limit
            alt = c.transition(alt, '0-metar_wind_alt', elapsed, 0.3048) # 1f/s
            
            # Fix temperatures    
            if 'temperature' in self.weatherData['metar']:
                if self.weatherData['metar']['temperature'][0] is not False:
                    extra['temp'] = self.weatherData['metar']['temperature'][0] + 273.15
                if self.weatherData['metar']['temperature'][1] is not False:
                    extra['dew'] = self.weatherData['metar']['temperature'][1] + 273.15
            
            winds = [[alt, hdg, speed, extra]] + winds
            
        # Search current top and bottom layer:
        blayer = False
        nlayers = len(winds)
        
        if nlayers > 0:
            for i in range(len(winds)):
                tlayer = i
                if winds[tlayer][0] > self.alt:
                    break
                else:
                    blayer = i
            
            if self.windAlts != tlayer:
                # Layer change, reset transitions
                self.windAlts = tlayer
                c.transitionClearReferences(exclude = [str(blayer), str(tlayer)])
            
            twind = self.transWindLayer(winds[tlayer], str(tlayer), elapsed)
            
            if blayer is not False and blayer != tlayer:
                # We are between 2 layers, interpolate
                bwind = self.transWindLayer(winds[blayer], str(blayer), elapsed)
                rwind = self.interpolateWindLayer(twind, bwind, self.alt)
                
            else:
                # We are below the first layer or above the last one.
                rwind = twind;

        # Set layers
        self.setWindLayer(0, rwind)
        self.setWindLayer(1, rwind)
        self.setWindLayer(2, rwind)
  
        '''Set temperature and dewpoint.
        Use next layer if the data is not available'''
        
        extra = rwind[3]
        if nlayers > tlayer + 1:
            altLayer = winds[tlayer + 1]
        else:
            altLayer = False
        
        if 'temp' in extra:
            self.msltemp.value = c.oat2msltemp(extra['temp'] - 273.15, self.alt)
        elif altLayer and 'temp' in altLayer[3]:
            self.msltemp.value = c.oat2msltemp(altLayer[3]['temp'] - 273.15, altLayer[0])
        
        if 'dew' in extra:
            self.msldewp.value = c.oat2msltemp(extra['dew'] - 273.15, self.alt)
        elif altLayer and 'dew' in altLayer[3]:
            self.msldewp.value = c.oat2msltemp(altLayer[3]['dew'] - 273.15, altLayer[0])
            
        # Force shear direction 0
        self.winds[0]['gust_hdg'].value = 0
        self.winds[1]['gust_hdg'].value = 0
        self.winds[2]['gust_hdg'].value = 0
    
    def setWindLayer(self, index,  wlayer):
        alt, hdg, speed, extra = wlayer
        
        wind = self.winds[index]
        wind['hdg'].value, wind['speed'].value = hdg, speed
        
        if 'gust' in extra:
            wind['gust'].value = extra['gust']

    def transWindLayer(self, wlayer, id, elapsed):
        ''' Transition wind layer values'''
        alt, hdg, speed, extra = wlayer
        
        hdg = c.transitionHdg(hdg, id + '-hdg', elapsed, self.conf.windHdgTransSpeed)
        speed = c.transition(speed, id + '-speed', elapsed, self.conf.windHdgTransSpeed)
        
        # Extra vars
        for var in ['gust', 'rh', 'dew']:
            if var in extra:
                extra[var] = c.transition(extra[var], id + '-' + var , elapsed, self.conf.windGustTransSpeed)
        
        # Special cases
        if 'gust_hdg' in extra:
            extra['gust_hdg'] = 0
        
        return alt, hdg, speed, extra
    
    def setDrefIfDiff(self, dref, value, max_diff = False):
        ''' Set a dateref if the current value differs '''
        
        if max_diff:
            if abs(dref.value - value) > max_diff:
                dref.value = value
        else:
            if dref.value != value:
                dref.value = value
    
    def interpolateWindLayer(self, wlayer1, wlayer2, current_altitude):
        ''' Interpolates 2 wind layers 
        layer array: [alt, hdg, speed, extra] '''
        
        if wlayer1[0] == wlayer2[0]:
            return wlayer1
  
        layer = [0, 0, 0, {}]
        
        layer[0] = current_altitude
        layer[1] = c.interpolateHeading(wlayer1[1], wlayer2[1], wlayer1[0], wlayer2[0], current_altitude)
        layer[2] = c.interpolate(wlayer1[2], wlayer2[2], wlayer1[0], wlayer2[0], current_altitude)
        
        # Interpolate extras
        for key in wlayer1[3]:
            if key in wlayer2[3] and wlayer2[3][key] is not False:
                layer[3][key] = c.interpolate(wlayer1[3][key], wlayer2[3][key], wlayer1[0], wlayer2[0], current_altitude)
            else:
                # Leave null temp and dew if we can't interpolate
                if key not in ('temp', 'dew'):
                    layer[3][key] = wlayer1[3][key]
            
        return layer              
    
    def setClouds(self, cloudsr):
        # Clear = 0, High Cirrus = 1, Scattered = 2, Broken = 3, Overcast = 4, Stratus = 5
        xpClouds = { 
                    'FEW': [2, 2000], #[type, defaultHeight]
                    'SCT': [2, 2000],
                    'BKN': [3, 3000],
                    'OVC': [4, 3000],
                    }
        
        lastop = 0
        
        if self.weatherData and 'metar' in self.weatherData and 'clouds' in self.weatherData['metar']:
            clouds =  self.weatherData['metar']['clouds']
            i = 0            
            for cloud in clouds:
                # Search in gfs for a top level
                base, cover, type = cloud
                top = base + c.limit(xpClouds[cover][1], self.conf.max_cloud_height)
                
                for gfscloud in cloudsr:
                    gfsBase, gfsTop, gfsCover = gfscloud
                    if base < (gfsTop + 500) and base > (gfsBase - 500):
                        top = base + c.limit(gfsBase - gfsTop, self.conf.max_cloud_height)
                        break
                    else:
                        continue
                    
                self.setDrefIfDiff(self.clouds[i]['bottom'],  base, 100)
                self.setDrefIfDiff(self.clouds[i]['top'],  top, 1000)
                self.setDrefIfDiff(self.clouds[i]['coverage'],  xpClouds[cover][0])
                lastop = top
                i += 1
            if i < 3:
                for l in range(i, 3):
                    self.setDrefIfDiff(self.clouds[l]['coverage'], 0)
                    # Get cirrus from gfs
                    if i == 2 and cloudsr[2][1] > lastop and abs(cloudsr[2][1] - cloudsr[2][0]) < 600:
                        self.setDrefIfDiff(self.clouds[i]['bottom'],  cloudsr[2][0], 1000)
                        self.setDrefIfDiff(self.clouds[i]['top'],  cloudsr[2][1], 1000)
                        self.setDrefIfDiff(self.clouds[i]['coverage'],  1)
                    
        else:
            # Gfs clouds
            clouds = cloudsr[:]
            clouds.sort(reverse=True)
            cl = self.clouds
            if len(clouds) > 2:
                for i in range(3):
                    clayer  = clouds.pop()
                    if clayer[2] == '0':
                        cl[i]['coverage'].value = clayer[2]
                    else:
                        if int(cl[i]['bottom'].value) != int(clayer[0]) and cl[i]['coverage'].value != clayer[2]:
                            base, top, cover = clayer
                            self.setDrefIfDiff(self.clouds[i]['bottom'],  base, 100)
                            self.setDrefIfDiff(self.clouds[i]['top'], base + c.limit(top - base, self.conf.max_cloud_height), 100)
                            self.setDrefIfDiff(self.clouds[i]['coverage'], cover)
    
    def setPressure(self, pressure, elapsed):
        c.datarefTransition(self.pressure, pressure, elapsed, 0.005)
        
    @classmethod
    def cc2xp(self, cover):
        #Cloud cover to X-plane
        xp = cover/100.0*4
        if xp < 1 and cover > 0:
            xp = 1
        elif cover > 89:
            xp = 4
        return xp
 
class PythonInterface:
    '''
    Xplane plugin
    '''
    def XPluginStart(self):
        self.syspath = []
        self.conf = Conf(XPLMGetSystemPath(self.syspath)[:-1])
        
        self.Name = "noaWeather - " + self.conf.__VERSION__
        self.Sig = "noaWeather.joanpc.PI"
        self.Desc = "NOA GFS in x-plane"
         
        self.latdr  = EasyDref('sim/flightmodel/position/latitude', 'double')
        self.londr  = EasyDref('sim/flightmodel/position/longitude', 'double')
        self.altdr  = EasyDref('sim/flightmodel/position/elevation', 'double')
        
        self.weather = Weather(self.conf)
         
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
        
        # Flightloop counters
        self.flcounter = 0
        self.fltime = 1
        self.lastParse = 0
        
        self.aboutlines = 17
        
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
        x2 = x + 780
        y2 = y - 85 - 20 * 15
        Buffer = "X-Plane NOAA GFS Weather - %s  -- Thanks to all betatesters! --" % (self.conf.__VERSION__)
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

        y -=25
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
        
        # Pressure enalbe
        XPCreateWidget(x+5, y-40, x+20, y-60, 1, 'Pressure', 0, window, xpWidgetClass_Caption)
        self.pressureCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.pressureCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.pressureCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.pressureCheck, xpProperty_ButtonState, self.conf.set_pressure)
        y -= 20
        
        # Turbulence enable
        XPCreateWidget(x+5, y-40, x+20, y-60, 1, 'Turbulence', 0, window, xpWidgetClass_Caption)
        self.turbCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonState, self.conf.set_turb)
        y -= 30
        x -=5
        
        x1 = x+5

        # Metar source radios        
        XPCreateWidget(x, y-40, x+20, y-60, 1, 'METAR SOURCE', 0, window, xpWidgetClass_Caption)
        y -= 20
        XPCreateWidget(x1, y-40, x1+20, y-60, 1, 'NOAA', 0, window, xpWidgetClass_Caption)
        mtNoaCheck = XPCreateWidget(x1+40, y-40, x1+45, y-60, 1, '', 0, window, xpWidgetClass_Button)
        x1 += 52
        XPCreateWidget(x1, y-40, x1+20, y-60, 1, 'IVAO', 0, window, xpWidgetClass_Caption)
        mtIvaoCheck = XPCreateWidget(x1+35, y-40, x1+45, y-60, 1, '', 0, window, xpWidgetClass_Button)
        x1 += 50     
        XPCreateWidget(x1, y-40, x1+20, y-60, 1, 'VATSIM', 0, window, xpWidgetClass_Caption)
        mtVatsimCheck = XPCreateWidget(x1+45, y-40, x1+60, y-60, 1, '', 0, window, xpWidgetClass_Button)
        x1 += 52
        
        self.mtSourceChecks = {mtNoaCheck: 'NOAA',
                               mtIvaoCheck: 'IVAO',
                               mtVatsimCheck: 'VATSIM'
                               }
        
        for check in self.mtSourceChecks:
            XPSetWidgetProperty(check, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(check, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(check, xpProperty_ButtonState, int(self.conf.metar_source == self.mtSourceChecks[check]))
            
            
        y -= 30
        XPCreateWidget(x, y-40, x+80, y-60, 1, 'Metar AGL limit (ft)', 0, window, xpWidgetClass_Caption)
        self.transAltInput = XPCreateWidget(x+110, y-40, x+160, y-62, 1, c.convertForInput(self.conf.metar_agl_limit, 'm2ft'), 0, window, xpWidgetClass_TextField)
        XPSetWidgetProperty(self.transAltInput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.transAltInput, xpProperty_Enabled, 1)
        
        y -= 30
        XPCreateWidget(x, y-40, x+80, y-60, 1, 'Performance Tweaks', 0, window, xpWidgetClass_Caption)
        y -= 20
        XPCreateWidget(x+5, y-40, x+80, y-60, 1, 'Max Visibility (sm)', 0, window, xpWidgetClass_Caption)
        self.maxVisInput = XPCreateWidget(x+119, y-40, x+160, y-62, 1, c.convertForInput(self.conf.max_visibility, 'm2sm'), 0, window, xpWidgetClass_TextField)
        XPSetWidgetProperty(self.maxVisInput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.maxVisInput, xpProperty_Enabled, 1)
        y -= 20
        XPCreateWidget(x+5, y-40, x+80, y-60, 1, 'Max cloud height (ft)', 0, window, xpWidgetClass_Caption)
        self.maxCloudHeightInput = XPCreateWidget(x+119, y-40, x+160, y-62, 1, c.convertForInput(self.conf.max_cloud_height, 'm2ft'), 0, window, xpWidgetClass_TextField)
        XPSetWidgetProperty(self.maxCloudHeightInput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.maxCloudHeightInput, xpProperty_Enabled, 1)
        
        y -= 50
        # Save
        self.saveButton = XPCreateWidget(x+25, y-20, x+125, y-60, 1, "Apply & Save", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.saveButton, xpProperty_ButtonType, xpPushButton)
        
        x += 170
        y = top
        
        # ABOUT/ STATUS Sub Window
        subw = XPCreateWidget(x+10, y-30, x2-20 + 10, y - (19 * self.aboutlines), 1, "" ,  0,window, xpWidgetClass_SubWindow)
        # Set the style to sub window
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        x += 20
        y -= 20
        
        # Add Close Box decorations to the Main Widget
        XPSetWidgetProperty(window, xpProperty_MainWindowHasCloseBoxes, 1)
        
        # Create status captions
        self.statusBuff = []
        for i in range(self.aboutlines):
            y -= 15
            self.statusBuff.append(XPCreateWidget(x, y, x+40, y-20, 1, '--', 0, window, xpWidgetClass_Caption))
            
        self.updateStatus()
        # Enable download
        
        y -= 20
        XPCreateWidget(x, y, x+20, y-20, 1, 'Download latest data', 0, window, xpWidgetClass_Caption)
        self.downloadCheck = XPCreateWidget(x+120, y, x+130, y-20, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.downloadCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.downloadCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.downloadCheck, xpProperty_ButtonState, self.conf.download)
        
        # DumpLog Button
        self.dumpLogButton = XPCreateWidget(x+160, y, x+260, y-20, 1, "DumpLog", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.dumpLogButton, xpProperty_ButtonType, xpPushButton)
        
        self.dumpLabel = XPCreateWidget(x+270, y, x+380, y-20, 1, '', 0, window, xpWidgetClass_Caption)
        
        y -= 30
        subw = XPCreateWidget(x-10, y, x2-20 + 10, y2 +15, 1, "" ,  0,window, xpWidgetClass_SubWindow)
        x += 10
        # Set the style to sub window
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        sysinfo = [
        'X-Plane NOAA Weather: %s' % self.conf.__VERSION__,
        '(c) joan perez i cauhe 2012-15',
        ]
        for label in sysinfo:
            XPCreateWidget(x, y-5, x+120, y-20, 1, label, 0, window, xpWidgetClass_Caption)
            y -= 15
            
        # Visit site Button
        x += 240
        y += 15
        self.aboutVisit = XPCreateWidget(x, y, x+100, y-20, 1, "Official site", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.aboutVisit, xpProperty_ButtonType, xpPushButton)
        
        # Donate Button
        self.donate = XPCreateWidget(x+130, y, x+230, y-20, 1, "Donate", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.donate, xpProperty_ButtonType, xpPushButton)
            
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
        
        if inMessage == xpMsg_ButtonStateChanged and inParam1 in self.mtSourceChecks:
            if inParam2:
                for i in self.mtSourceChecks:
                    if i != inParam1:
                        XPSetWidgetProperty(i, xpProperty_ButtonState, 0)
            else:
                XPSetWidgetProperty(inParam1, xpProperty_ButtonState, 1)
            return 1

        # Handle any button pushes
        if (inMessage == xpMsg_PushButtonPressed):

            if (inParam1 == self.aboutVisit):
                from webbrowser import open_new
                open_new('http://x-plane.joanpc.com/');
                return 1
            if (inParam1 == self.donate):
                from webbrowser import open_new
                open_new('https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=ZQL6V9YLKRFEJ&lc=US&item_name=joan%20x%2dplane%20developer&item_number=XP%20NOAA%20Weather&currency_code=EUR&bn=PP%2dDonationsBF%3abtn_donateCC_LG%2egif%3aNonHosted');
                return 1
            if inParam1 == self.saveButton:
                # Save configuration
                self.conf.enabled       = XPGetWidgetProperty(self.enableCheck, xpProperty_ButtonState, None)
                self.conf.set_wind      = XPGetWidgetProperty(self.windsCheck, xpProperty_ButtonState, None)
                self.conf.set_clouds    = XPGetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, None)
                self.conf.set_temp      = XPGetWidgetProperty(self.tempCheck, xpProperty_ButtonState, None)
                self.conf.set_pressure  = XPGetWidgetProperty(self.pressureCheck, xpProperty_ButtonState, None)
                self.conf.set_turb      = XPGetWidgetProperty(self.turbCheck, xpProperty_ButtonState, None)
                
                self.conf.download      = XPGetWidgetProperty(self.downloadCheck, xpProperty_ButtonState, None)
                
                buff = []
                XPGetWidgetDescriptor(self.transAltInput, buff, 256)
                self.conf.metar_agl_limit = c.convertFromInput(buff[0], 'f2m', 900)

                buff = []
                XPGetWidgetDescriptor(self.maxCloudHeightInput, buff, 256)
                self.conf.max_cloud_height = c.convertFromInput(buff[0], 'f2m')
                
                buff = []
                XPGetWidgetDescriptor(self.maxVisInput, buff, 256)
                self.conf.max_visibility = c.convertFromInput(buff[0], 'sm2m')
                
                # Check metar source
                prev_metar_source = self.conf.metar_source
                for check in self.mtSourceChecks:
                    if XPGetWidgetProperty(check, xpProperty_ButtonState, None):
                        self.conf.metar_source = self.mtSourceChecks[check]
                
                # Save config and tell server to reload it
                self.conf.pluginSave()
                self.weather.weatherClientSend('!reload')
                
                # If metar source has changed tell server to reinit metar database
                if self.conf.metar_source != prev_metar_source:
                    self.weather.weatherClientSend('!resetMetar')
   
                self.weather.startWeatherClient()
                self.aboutWindowUpdate()
                c.transitionClearReferences()
                return 1
            if inParam1 == self.dumpLogButton:
                dumpfile = self.dumpLog()
                XPSetWidgetDescriptor(self.dumpLabel, os.sep.join(dumpfile.split(os.sep)[-3:]))
                return 1
        return 0
    
    def aboutWindowUpdate(self):
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonState, self.conf.enabled)
        XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonState, self.conf.set_wind)
        XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, self.conf.set_clouds)
        XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonState, self.conf.set_temp)
        
        XPSetWidgetDescriptor(self.transAltInput, c.convertForInput(self.conf.metar_alg_limit, 'm2f'))
        XPSetWidgetDescriptor(self.maxVisInput, c.convertForInput(self.conf.max_visibility, 'm2sm'))
        XPSetWidgetDescriptor(self.maxCloudHeightInput, c.convertForInput(self.conf.max_cloud_height, 'm2ft'))
        
        #for check in self.mtSourceChecks:
        #    if check == self.conf.metar_source:
        #        XPSetWidgetProperty(check, xpProperty_ButtonState, 1)
        #    else:
        #        XPSetWidgetProperty(check, xpProperty_ButtonState, 0)

        self.updateStatus()
        
    def updateStatus(self):
        '''Updates status window'''
        
        sysinfo = self.weatherInfo()
        
        i = 0
        for label in sysinfo:
            XPSetWidgetDescriptor(self.statusBuff[i], label)
            i +=1
            if i > self.aboutlines -1:
                break
    
    def weatherInfo(self):
        '''Return an array of strings with formated weather data'''
        
        sysinfo = []
        
        if not self.weather.weatherData:
            sysinfo = ['Data not ready. Please wait.']
        else:
            wdata = self.weather.weatherData
            if 'info' in wdata:
                sysinfo = [
                           'XPNoaaWeather %s Status:' % self.conf.__VERSION__,
                           '    LAT: %.2f/%.2f LON: %.2f/%.2f MAGNETIC DEV: %.2f' % (self.latdr.value , wdata['info']['lat'], self.londr.value, wdata['info']['lon'], self.weather.mag_deviation.value),
                           '    GFS Cycle: %s' % (wdata['info']['gfs_cycle']),
                           '    WAFS Cycle: %s' % (wdata['info']['wafs_cycle']),
                ]
        
            if 'metar' in wdata and 'icao' in wdata['metar']:
                
                # Split metar if needed
                splitlen = 80
                metar = 'METAR STATION: %s %s' % (wdata['metar']['icao'], wdata['metar']['metar'])
                
                if len(metar) > splitlen:
                    icut = metar.rfind(' ', 0, splitlen)
                    sysinfo += [metar[:icut], metar[icut+1:]]
                else:
                    sysinfo += [metar]
                    
                sysinfo += [
                            '    Airport altitude: %dft, gfs switch alt: %dft' % (wdata['metar']['elevation'] * 3.28084, (wdata['metar']['elevation'] + self.conf.metar_agl_limit) * 3.28084 ),
                            '    Temp: %s, Dewpoint: %s, ' % (c.strFloat(wdata['metar']['temperature'][0]), c.strFloat(wdata['metar']['temperature'][1])) +
                            'Visibility: %d m, ' % (wdata['metar']['visibility']) +
                            'Press: %s inhg ' % (c.strFloat(wdata['metar']['pressure'])),
                            '    Wind:  %d %dkt, gust +%dkt' % (wdata['metar']['wind'][0], wdata['metar']['wind'][1], wdata['metar']['wind'][2])
                           ]
                if 'precipitation' in wdata['metar'] and len(wdata['metar']['precipitation']):
                    precip = ''
                    for type in wdata['metar']['precipitation']:
                        precip += '%s%s ' % (wdata['metar']['precipitation'][type]['int'], type)
                
                    sysinfo += ['Precipitation: %s' % (precip)]
                if 'clouds' in wdata['metar']:
                    clouds = '    Clouds: BASE|COVER    '
                    for cloud in wdata['metar']['clouds']:
                        alt, coverage, type = cloud
                        clouds += '%03d|%s%s ' % (alt * 3.28084 / 100, coverage, type)
                    sysinfo += [clouds]
                     
            if 'gfs' in wdata:          
                if 'winds' in wdata['gfs']:
                    sysinfo += ['GFS WIND LAYERS: %i FL|HDG|KT|TEMP' % (len(wdata['gfs']['winds']))]
                    wlayers = ''
                    i = 0
                    for layer in wdata['gfs']['winds']:
                        i += 1
                        alt, hdg, speed, extra = layer
                        wlayers += '   %03d|%03d|%02dkt|%02d ' % (alt * 3.28084 / 100, hdg, speed, extra['temp'] - 273.15 )
                        if i > 3:
                            i = 0
                            sysinfo += [wlayers]
                            wlayers = ''    
                    if i > 0:
                        sysinfo += [wlayers]
                    
                if 'clouds' in wdata['gfs']:
                    clouds = 'GFS CLOUDS  FLBASE|FLTOP|COVER'
                    sclouds = ''
                    for layer in wdata['gfs']['clouds']:
                        top, bottom, cover = layer
                        if top > 0:
                            sclouds = '   %03d|%03d|%.2f ' % (top * 3.28084/100, bottom * 3.28084/100, cover) 
                    sysinfo += [clouds, sclouds]
            
            if 'wafs' in wdata:
                tblayers = ''
                for layer in wdata['wafs']:
                    tblayers += '   %03d|%.1f ' % (layer[0] * 3.28084 / 100, layer[1]) 
                
                sysinfo += ['WAFS TURBULENCE: FL|SEV %d' % (len(wdata['wafs'])), tblayers]
        
        return sysinfo
    
    def dumpLog(self):
        ''' Dumps all the information to a file to report bugs'''
        
        dumpath = os.sep.join([self.conf.cachepath, 'dumplogs'])
        
        if not os.path.exists(dumpath):
            os.makedirs(dumpath)
        
        dumplog = os.sep.join([dumpath, datetime.utcnow().strftime('%Y%m%d_%H%M%SZdump.txt')])
         
        f = open(dumplog, 'w')
        
        import platform
        from pprint import pprint
        
        output = ['--- Platform Info ---\n',
                  'Plugin version: %s\n' % self.conf.__VERSION__,
                  'Platform: %s\n' % (platform.platform()),
                  'Python version: %s\n' % (platform.python_version()),
                  '\n--- Weather Status ---\n'
                  ]
        
        for line in self.weatherInfo():
            output.append('%s\n' % line)
    
        output += ['\n--- Weather Data ---\n']
        
        for line in output:
            f.write(line)
        
        pprint(self.weather.weatherData, f, width=160)
        f.write('\n--- Transition data Data --- \n')
        pprint(c.transrefs, f, width=160)
        
        
        f.write('\n--- Weather Datarefs --- \n')
        # Dump winds datarefs
        datarefs = {'winds': self.weather.winds,
                     'clouds': self.weather.clouds,
                     }
        
        pdrefs = {}
        for item in datarefs:
            pdrefs[item] = []
            for i in range(len(datarefs[item])):
                wdata = {}
                for key in datarefs[item][i]:
                    wdata[key] = datarefs[item][i][key].value
                pdrefs[item].append(wdata)
        pprint(pdrefs, f, width=160)
        
        vars = {}
        f.write('\n')    
        for var in self.weather.__dict__:
            if isinstance(self.weather.__dict__[var], EasyDref):
                vars[var] = self.weather.__dict__[var].value       
        pprint(vars, f, width=160)     
        
        f.write('\n--- Configuration ---\n')
        vars = {}
        for var in self.conf.__dict__:
            if type(self.conf.__dict__[var]) in (str, int, float, list, tuple, dict):
                vars[var] = self.conf.__dict__[var]
        pprint(vars, f, width=160)     
                
        f.close()
        
        return dumplog

    def floopCallback(self, elapsedMe, elapsedSim, counter, refcon):
        '''
        Floop Callback
        '''
        
        # Request data from the weather server
        self.flcounter += elapsedMe
        self.fltime += elapsedMe
        if self.flcounter > self.conf.parserate and self.weather.weatherClientThread:
            
            lat, lon = round(self.latdr.value, 1), round(self.londr.value, 1)
            
            # Request data on postion change, every 0.1 degree or 60 seconds 
            if (lat, lon) != (self.weather.last_lat, self.weather.last_lon) or (self.fltime - self.lastParse) > 60:
                self.weather.last_lat, self.weather.last_lon = lat, lon
                
                self.weather.weatherClientSend("?%.2f|%.2f\n" % (lat, lon))
                
                self.flcounter = 0
                self.lastParse = self.fltime
        
        if self.aboutWindow and XPIsWidgetVisible(self.aboutWindowWidget):
            self.updateStatus()
        
        if not self.conf.enabled or not self.weather.weatherData:
            return -1
        
        # Store altitude
        self.weather.alt = self.altdr.value
        
        wdata = self.weather.weatherData
        
        pressSet = False
        
        rain, ts = 0, 0
        
        if self.weather.newData:
            # Set metar values
            if 'metar' in wdata:
                if 'visibility' in wdata['metar']:
                    self.weather.visibility.value =  c.limit(wdata['metar']['visibility'], self.conf.max_visibility)
                if 'pressure' in wdata['metar'] and wdata['metar']['pressure'] is not False:
                    self.weather.setPressure(wdata['metar']['pressure'], elapsedMe)
                    pressSet = True
                if'precipitation' in wdata['metar'] and len(wdata['metar']['precipitation']):
                    precp = wdata['metar']['precipitation']
                    if 'RA'in precp:
                        rain = c.metar2xpprecipitation('RA', precp['RA']['int'], precp['RA']['mod'])
                    if 'SN'in precp:
                        rain = c.metar2xpprecipitation('RA', precp['SN']['int'], precp['SN']['mod'])
                    if 'TS' in precp:
                        ts = 0.5
                        if  precp['TS']['int'] == '-':
                            ts = 0.25
                        elif precp['TS']['int'] == '+':
                            ts = 1
            
            self.weather.thunderstorm.value = ts
            self.weather.precipitation.value = rain
        
        if 'gfs' in wdata:    
            # Set winds and clouds
            if self.conf.set_wind and 'winds' in wdata['gfs']:
                self.weather.setWinds(wdata['gfs']['winds'], elapsedMe)
            if self.weather.newData and self.conf.set_clouds and 'clouds' in wdata['gfs']:
                self.weather.setClouds(wdata['gfs']['clouds'])
            # Set pressure
            if not pressSet and self.conf.set_pressure and 'pressure' in wdata['gfs']:
                self.weather.setPressure(wdata['gfs']['pressure'], elapsedMe)
        
        if self.conf.set_turb and 'wafs' in wdata:
            self.weather.setTurbulence(wdata['wafs'])
            
        self.weather.newData = False
            
        return -1
    
    def XPluginStop(self):
        # Destroy windows
        if self.aboutWindow:
            XPDestroyWidget(self, self.aboutWindowWidget, 1)
        
        XPLMUnregisterFlightLoopCallback(self, self.floop, 0)
        
        # kill weather server/client
        self.weather.shutdown()
        
        XPLMDestroyMenu(self, self.mMain)
        self.conf.pluginSave()
        
    def XPluginEnable(self):
        return 1
    
    def XPluginDisable(self):
        pass
    
    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        if (inParam == XPLM_PLUGIN_XPLANE and inMessage == XPLM_MSG_AIRPORT_LOADED):
            self.weather.startWeatherClient()
            c.transitionClearReferences()
