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
        
        self.windAlts = (0, 0)
        
        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.die = threading.Event()
        self.lock = threading.Lock()
        
        self.startWeatherServer()
        
        self.tlayer = False
        self.blayer = False
    
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
            if self.die.is_set():
                return
    
    def startWeatherServer(self):
        DETACHED_PROCESS = 0x00000008
        args = [self.conf.pythonpath, os.sep.join([self.conf.respath, 'weatherServer.py']), self.conf.syspath]
        
        if self.conf.spinfo:
            subprocess.Popen(args, startupinfo=self.conf.spinfo, close_fds=True, creationflags=DETACHED_PROCESS)
        else:
            subprocess.Popen(args, close_fds=True)
    
    def shutdownWeatherServer(self):
        self.sock.sendto("!shutdown", ('127.0.0.1', self.conf.server_port))
    
    def shutdown(self):
        # Shutdown client and server
        self.die.set()
        self.shutdownWeatherServer()
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
    
    def setWinds(self, winds, elapsed, setTemp = True):
        '''Set winds'''  
        
        metarAlt = False
        
        # Add metar layer 
        if 'metar' in self.weatherData and 'wind' in self.weatherData['metar']:
            alt = self.weatherData['metar']['elevation']
            hdg, speed, gust = self.weatherData['metar']['wind']
            extra = {'gust': gust}

            # Transition metar layer altitude
            metarAlt = alt
            alt = c.transition(alt, 'metar_wind_alt', elapsed, 1) + self.conf.metar_agl_limit

            if 'temperature' in self.weatherData['metar']:    
                extra['temp'] = self.weatherData['metar']['temperature'][0] + 274.15
                extra['dew'] = self.weatherData['metar']['temperature'][1] + 274.15
            
            winds = [[alt, hdg, speed, extra]] + winds
            
        # Search current top and bottom layer:
        blayer = False
        
        if len(winds) > 1:
            for wind in range(len(winds)):
                tlayer = winds[wind]
                if tlayer[0] > self.alt:
                    break
                else:
                    blayer = tlayer
            if blayer:
                if self.windAlts != (tlayer[0], blayer[0]):
                    # Layer change, don't transition     
                    self.windAlts = (tlayer[0], blayer[0])
                    # Reset references
                    c.transitionClearReferences()
                else:
                    # Transition both layers
                    tlayer = self.transWindLayer(tlayer, 'top_wind_layer_', elapsed)
                    blayer = self.transWindLayer(blayer, 'bottom_wind_layer_', elapsed)
                    pass
                    
                # Interpolate if whe are above
                if blayer[0] < self.alt:
                    # TODO: add metar trans
                    layer = self.interpolateWindLayer(tlayer, blayer, self.alt)
                else:
                    layer = tlayer
                
            else:
                tlayer = self.transWindLayer(tlayer, 'top_wind_layer_', elapsed)
                layer = tlayer;
            
        # Set layers
        self.setWindLayer(0, layer)
        self.setWindLayer(1, layer)
        self.setWindLayer(2, layer)
        
        extra = layer[3]
        
        # Fix metar altitude
        if metarAlt != False:
            alt = metarAlt
        else:
            alt = layer[0]
        
        if 'dew' in extra:
            self.msldewp.value = c.oat2msltemp(extra['dew'], alt)
        if 'temp' in extra:
            self.msltemp.value = c.oat2msltemp(extra['temp'], alt)
    
    def setWindLayer(self, index,  wlayer):
        alt, hdg, speed, extra = wlayer
        
        wind = self.winds[index]
        wind['hdg'].value, wind['speed'].value = hdg, speed
        
        if 'gust' in extra:
            wind['gust'].value = extra['gust']
                
    def transWindLayer(self, wlayer, id, elapsed):
        ''' Transition wind layer values'''
        alt, hdg, speed, extra = wlayer
        
        hdg = c.transitionHdg(hdg, id + '_hdg', elapsed, self.conf.windHdgTransSpeed)
        speed = c.transition(speed, id + '_speed', elapsed, self.conf.windHdgTransSpeed)
        
        # Extra vars
        for var in ['gust', 'rh', 'dew']:
            if var in extra:
                extra[var] = c.transition(extra[var], id + '_' + var , elapsed, self.conf.windGustTransSpeed)
        
        # Special cases
        if 'gust_hdg' in extra:
            extra['gust_hdg'] = 0
        
        return alt, hdg, speed, extra
              
    
    def interpolateWindLayer(self, wlayer1, wlayer2, current_altitude):
        ''' Interpolates 2 wind layers 
        layer array: [alt, hdg, speed, extra] '''
  
        layer = [0, 0, 0, {}]
        
        layer[0] = current_altitude
        layer[1] = c.interpolateHeading(wlayer1[1], wlayer2[1], wlayer1[0], wlayer2[0], current_altitude)
        layer[2] = c.interpolate(wlayer1[2], wlayer2[2], wlayer1[0], wlayer2[0], current_altitude)
        
        # Interpolate extras
        for key in wlayer1[3]:
            if key in wlayer2[3]:
                layer[3][key] = c.interpolate(wlayer1[3][key], wlayer2[3][key], wlayer1[0], wlayer2[0], current_altitude)
            else:
                layer[3][key] = wlayer1[3][key]
            
        return layer              
    
    def setClouds(self, cloudsr):
        # Clear = 0, High Cirrus = 1, Scattered = 2, Broken = 3, Overcast = 4, Stratus = 5
        xpClouds = {
                    'FEW': 2,
                    'SCT': 2,
                    'BKN': 3,
                    'OVC': 4,
                    }
        
        # Metar clouds disabled
        if False and self.weatherData and 'metar' in self.weatherData and 'clouds' in self.weatherData['metar']:
            
            i = 0
            for cloud in self.weatherData['metar']['clouds']:
                
                alt, coverage, type = cloud
                
                if coverage in xpClouds:
                    coverage = xpClouds[coverage]
                else:
                    coverage = xpClouds['FEW']
                
                self.clouds[i]['bottom'].value = alt
                self.clouds[i]['top'].value = alt + 3000
                self.clouds[i]['coverage'].value = coverage
                
                i += 1
        
        else:
            # Gfs clouds
            clouds = cloudsr[:]
            clouds.sort(reverse=True)
            if len(clouds) > 2:
                for i in range(3):
                    clayer  = clouds.pop()
                    cl = self.clouds
                    if clayer[2] == '0':
                        cl[i]['coverage'].value = clayer[2]
                    else:
                        if int(cl[i]['bottom'].value) != int(clayer[0]) and cl[i]['coverage'].value != clayer[2]:
                            cl[i]['bottom'].value, cl[i]['top'].value, cl[i]['coverage'].value  = clayer
    
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
        
        self.aboutlines = 16
        
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
        Buffer = "X-Plane NOAA GFS Weather - %s" % (self.conf.__VERSION__)
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
        
        # VATSIM Compatible
        #XPCreateWidget(x, y-40, x+20, y-60, 1, 'VATSIM compat', 0, window, xpWidgetClass_Caption)
        #self.vatsimCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
        #XPSetWidgetProperty(self.vatsimCheck, xpProperty_ButtonType, xpRadioButton)
        #XPSetWidgetProperty(self.vatsimCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        #XPSetWidgetProperty(self.vatsimCheck, xpProperty_ButtonState, self.conf.vatsim)
        #y -= 20
        
        # trans altitude
        #XPCreateWidget(x, y-40, x+80, y-60, 1, 'Switch to METAR', 0, window, xpWidgetClass_Caption)
        #self.metarCheck = XPCreateWidget(x+110, y-40, x+120, y-60, 1, '', 0, window, xpWidgetClass_Button)
        #XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonType, xpRadioButton)
        #XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        #XPSetWidgetProperty(self.metarCheck, xpProperty_ButtonState, self.conf.use_metar)
        
        #y -= 20
        #XPCreateWidget(x+20, y-40, x+80, y-60, 1, 'Below FL', 0, window, xpWidgetClass_Caption)
        #self.transAltInput = XPCreateWidget(x+100, y-40, x+140, y-62, 1, '%i' % (self.conf.transalt*3.2808399/100), 0, window, xpWidgetClass_TextField)
        #XPSetWidgetProperty(self.transAltInput, xpProperty_TextFieldType, xpTextEntryField)
        #XPSetWidgetProperty(self.transAltInput, xpProperty_Enabled, 1)
        
        y -= 35
        # Save
        self.saveButton = XPCreateWidget(x+25, y-20, x+125, y-60, 1, "Apply & Save", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.saveButton, xpProperty_ButtonType, xpPushButton)
        
        x += 170
        y = top
        
        # ABOUT/ STATUS Sub Window
        subw = XPCreateWidget(x+10, y-30, x2-20 + 10, y - (20 * self.aboutlines), 1, "" ,  0,window, xpWidgetClass_SubWindow)
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
        
        y -= 40
        subw = XPCreateWidget(x-10, y, x2-20 + 10, y2 +15, 1, "" ,  0,window, xpWidgetClass_SubWindow)
        x += 10
        # Set the style to sub window
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        sysinfo = [
        'X-Plane NOAA Weather: %s' % self.conf.__VERSION__,
        '(c) joan perez cauhe 2012-15',
        ]
        for label in sysinfo:
            y -= 10
            XPCreateWidget(x, y, x+40, y-20, 1, label, 0, window, xpWidgetClass_Caption)
            
        # Visit site Button
        x += 240
        self.aboutVisit = XPCreateWidget(x, y, x+100, y-20, 1, "Visit site", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.aboutVisit, xpProperty_ButtonType, xpPushButton)
        
        # Donate Button
        self.donate = XPCreateWidget(x+130, y, x+230, y-20, 1, "Donate", 0, window, xpWidgetClass_Button)
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
                open_new('http://x-plane.joanpc.com/');
                return 1
            elif (inParam1 == self.donate):
                from webbrowser import open_new
                open_new('https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=ZQL6V9YLKRFEJ&lc=US&item_name=joan%20x%2dplane%20developer&item_number=XP%20NOAA%20Weather&currency_code=EUR&bn=PP%2dDonationsBF%3abtn_donateCC_LG%2egif%3aNonHosted');
                return 1
            elif inParam1 == self.saveButton:
                # Save configuration
                self.conf.enabled       = XPGetWidgetProperty(self.enableCheck, xpProperty_ButtonState, None)
                self.conf.set_wind      = XPGetWidgetProperty(self.windsCheck, xpProperty_ButtonState, None)
                self.conf.set_clouds    = XPGetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, None)
                self.conf.set_temp      = XPGetWidgetProperty(self.tempCheck, xpProperty_ButtonState, None)
                self.conf.set_pressure  = XPGetWidgetProperty(self.pressureCheck, xpProperty_ButtonState, None)
                self.conf.set_turb      = XPGetWidgetProperty(self.turbCheck, xpProperty_ButtonState, None)
                #self.conf.use_metar     = XPGetWidgetProperty(self.metarCheck, xpProperty_ButtonState, None)
                #self.conf.vatsim        = XPGetWidgetProperty(self.vatsimCheck, xpProperty_ButtonState, None)
                self.conf.download      = XPGetWidgetProperty(self.downloadCheck, xpProperty_ButtonState, None)
                
                buff = []
                XPGetWidgetDescriptor(self.transAltInput, buff, 256)
                #self.conf.transalt = c.toFloat(buff[0], 100) * 0.3048 * 100
                #buff = []
                #XPGetWidgetDescriptor(self.updateRateInput, buff, 256)
                #self.conf.updaterate = c.toFloat(buff[0], 1)
                
                #if self.conf.vatsim: 
                #     self.conf.set_clouds = False
                #    self.conf.set_temp   = False
                #    self.conf.use_metar  = False
                #    self.weather.winds[0]['alt'].value = self.conf.transalt
                
                #if not self.conf.use_metar:
                #    self.weather.xpWeatherOn.value = 0
                #else:
                #    self.weather.winds[0]['alt'].value = self.conf.transalt   
                
                self.conf.save()
                
                self.weather.startWeatherClient()
                
                self.aboutWindowUpdate()
                return 1
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
        
        if not self.weather.weatherData:
            sysinfo = ['Data not ready. Please wait.']
        else:
            wdata = self.weather.weatherData
            if 'info' in wdata:
                sysinfo = [
                           'XPGFS Status:',
                           'lat: %.2f/%.2f lon: %.2f/%.2f magnetic deviation: %2.f' % (self.latdr.value , wdata['info']['lat'], self.londr.value, wdata['info']['lon'], self.weather.mag_deviation.value),
                           'GFS Cycle: %s' % (wdata['info']['gfs_cycle']),
                           'WAFS Cycle: %s' % (wdata['info']['wafs_cycle']),
                ]
        
            if 'metar' in wdata and 'icao' in wdata['metar']:
                
                # Split metar if needed
                splitlen = 80
                metar = 'Metar station: %s %s' % (wdata['metar']['icao'], wdata['metar']['metar'])
                
                if len(metar) > splitlen:
                    icut = metar.rfind(' ', 0, splitlen)
                    sysinfo += [metar[:icut], metar[icut+1:]]
                else:
                    sysinfo += [metar]
                    
                sysinfo += [
                            'Airport altitude: %dft, gfs switch alt: %dft' % (wdata['metar']['elevation'] * 3.28084, (wdata['metar']['elevation'] + self.conf.metar_agl_limit) * 3.28084 ),
                            'Temperature: %.1f, Dewpoint: %.1f, ' % (wdata['metar']['temperature'][0], wdata['metar']['temperature'][1]) +
                            'Visibility: %d meters, ' % (wdata['metar']['visibility']) +
                            'Pressure: %.2f inhg ' % (wdata['metar']['pressure']),
                            'Wind:  %d %dkt, gust +%dkt' % (wdata['metar']['wind'][0], wdata['metar']['wind'][1], wdata['metar']['wind'][2])
                           ]
                if 'precipitation' in wdata['metar'] and len(wdata['metar']['precipitation']):
                    precip = ''
                    for type in wdata['metar']['precipitation']:
                        precip += '%s%s ' % (wdata['metar']['precipitation'][type]['int'], type)
                
                    sysinfo += ['Precipitation: %s' % (precip)]
                if 'clouds' in wdata['metar']:
                    clouds = 'Clouds: '
                    for cloud in wdata['metar']['clouds']:
                        alt, coverage, type = cloud
                        clouds += '%d/%s%s ' % (alt * 3.28084 / 100, coverage, type)
                    sysinfo += [clouds]
                     
            if 'gfs' in wdata:          
                if 'winds' in wdata['gfs']:
                    sysinfo += ['Wind layers: %i FL/HDG/KT' % (len(wdata['gfs']['winds']))]
                    wlayers = ''
                    i = 0
                    for layer in wdata['gfs']['winds']:
                        i += 1
                        alt, hdg, speed = layer[0], layer[1], layer[2]
                        wlayers += 'FL%d/%03d/%d ' % (alt * 3.28084 / 100, hdg, speed)
                        if i > 5:
                            i = 0
                            sysinfo += [wlayers]
                            wlayers = ''    
                    if i > 0:
                        sysinfo += [wlayers]
                if 'clouds' in wdata['gfs']:
                    clouds = 'Clouds  base/top/cover '
                    for layer in wdata['gfs']['clouds']:
                        top, bottom, cover = layer
                        if top > 0:
                            clouds += '%d/%d/%.2f ' % (top * 3.28084/100, bottom * 3.28084/100, cover) 
                    sysinfo += [clouds]
            
            if 'wafs' in wdata:
                tblayers = ''
                for layer in wdata['wafs']:
                    tblayers += 'FL%d/%.1f ' % (layer[0] * 3.28084 / 100, layer[1]) 
                
                sysinfo += [
                            #'WAFS Cycle: %s' % (lastwafsgrib[0]),
                            'Turbulence layers: %d' % (len(wdata['wafs'])),
                            tblayers
                            ]
        
        i = 0
        for label in sysinfo:
            XPSetWidgetDescriptor(self.statusBuff[i], label)
            i +=1
            if i > self.aboutlines -1:
                break

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
                
                self.weather.sock.sendto("?%.2f|%.2f\n" % (lat, lon),
                                        ('127.0.0.1', self.conf.server_port))
                
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
        tempSet = False
        
        rain, ts = 0, 0
        
        # Set metar values
        if 'metar' in wdata:
            if 'visibility' in wdata['metar']:
                self.weather.visibility.value =  c.limit(wdata['metar']['visibility'], self.conf.max_visibility)
            if 'pressure' in wdata['metar']:
                self.weather.setPressure(wdata['metar']['pressure'], elapsedMe)
                pressSet = True
            if 'temperature' in wdata['metar'] and self.weather.alt < (self.conf.metar_agl_limit + wdata['metar']['elevation']):
                # Set metar temperature below 5000m
                temp, dew = wdata['metar']['temperature']
            
                self.weather.msltemp.value = c.oat2msltemp(temp + 274.15, wdata['metar']['elevation'])
                self.weather.msldewp.value = c.oat2msltemp(temp + 274.15, wdata['metar']['elevation'])
                tempSet = True
                
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
                self.weather.setWinds(wdata['gfs']['winds'], elapsedMe, not tempSet)
            if self.conf.set_clouds and 'clouds' in wdata['gfs']:
                self.weather.setClouds(wdata['gfs']['clouds'])
            # Set pressure
            if not pressSet and self.conf.set_pressure and 'pressure' in wdata['gfs']:
                self.weather.setPressure(wdata['gfs']['pressure'], elapsedMe)
        
        if self.conf.set_turb and 'wafs' in wdata:
            self.weather.setTurbulence(wdata['wafs'])
            
        return -1
    
    def XPluginStop(self):
        # Destroy windows
        if self.aboutWindow:
            XPDestroyWidget(self, self.aboutWindowWidget, 1)
        
        XPLMUnregisterFlightLoopCallback(self, self.floop, 0)
        
        # kill weather server/client
        self.weather.shutdown()
        
        XPLMDestroyMenu(self, self.mMain)
        self.conf.save()
        
    def XPluginEnable(self):
        return 1
    
    def XPluginDisable(self):
        pass
    
    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        if (inParam == XPLM_PLUGIN_XPLANE and inMessage == XPLM_MSG_AIRPORT_LOADED):
            self.weather.startWeatherClient()
