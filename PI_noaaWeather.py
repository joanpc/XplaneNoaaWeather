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
import time

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
        self.dewpoint    = EasyDref('sim/weather/dewpoi_sealevel_c', 'float')
        self.thermalAlt  = EasyDref('sim/weather/thermal_altitude_msl_m', 'float')
        self.visibility  = EasyDref('sim/weather/visibility_reported_m', 'float')
        self.pressure    = EasyDref('sim/weather/barometer_sealevel_inhg', 'float')
        
        self.precipitation = EasyDref('sim/weather/rain_percent', 'float')
        self.thunderstorm = EasyDref('sim/weather/thunderstorm_percent', 'float')
        
        # Data
        self.weatherData = False
        self.weatherClientThread = False
        
        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.die = threading.Event()
        self.lock = threading.Lock()
        
        self.startWeatherServer()
    
    def startWeatherClient(self):
        if not self.weatherClientThread:
            self.weatherClientThread = threading.Thread(target=self.weatherClient)
            self.weatherClientThread.start()
            
    def weatherClient(self):
        '''
        Wheather client thread fetches wheather from the server
        '''
        
        while True: #not self.die.wait(self.conf.parserate):
            
            lat, lon = round(self.lat, 2), round(self.lon, 2)
            
            #TODO: time refresh/push
            #if True or (self.last_lat, self.last_lon) != (lat, lon):
                
            self.last_lat, self.last_lon = lat, lon
            self.sock.sendto("?%f|%f\n" % (lat, lon), ('127.0.0.1', self.conf.server_port))
            received = self.sock.recv(1024*8)
        
            #self.lock.acquire() 
            self.weatherData = cPickle.loads(received)
            #self.lock.release()
            if self.die.is_set():
                return
            time.sleep(self.conf.parserate)
    
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
        self.weatherClientThread.join(3)
        self.shutdownWeatherServer()
    
    def setWindLayer(self, xpwind, layer, data, elapsed):
        # Sets wind layer and does transition if needed
        alt, hdg, speed = data[0], data[1], data[2]
        
        if 'gust' in data[3]:
            gust = data[3]['gust']
        else:
            gust = 0
        
        calt = xpwind['alt'].value

        if layer != 0 and abs(alt - calt) < 1000:
            # layer change trasition not needed xplane does interpolation
            xpwind['alt'].value, xpwind['hdg'].value, xpwind['speed'].value = alt, hdg, speed
            xpwind['gust'].value, xpwind['gust_hdg'].value = gust, 0
                        
        else:
            # do Transition
            c.datarefTransitionHdg(xpwind['hdg'], hdg, elapsed, self.conf.windHdgTransSpeed)
            c.datarefTransition(xpwind['speed'], speed, elapsed, self.conf.windHdgTransSpeed)
            c.datarefTransition(xpwind['gust'], gust, elapsed, self.conf.windGustTransSpeed)
            xpwind['gust_hdg'].value = 0
            
            if layer == 0:
                # Do altitude trasition for metar based wind layers 1m/s
                alt = c.limit(alt, max = 304.8) # XP minimum wind layer alt 1000 feet
                c.datarefTransition(xpwind['alt'], alt, elapsed, )
     
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
                self.setWindLayer(wl[1], 1, prevlayer, elapsed)
                self.setWindLayer(wl[2], 2, wlayer, elapsed)
            else:
                self.setWindLayer(wl[1], 1, wlayer, elapsed)

            # Set temperature
            if self.conf.set_temp and wlayer[3]['temp']:
                # Interpolate with previous layer
                if prevlayer and prevlayer[0] != wlayer[0] and wlayer[3]['temp']:
                    temp = c.interpolate(prevlayer[3]['temp'], wlayer[3]['temp'], prevlayer[0], wlayer[0], self.alt)
                    self.msltemp.value = temp
                else:
                    self.msltemp.value = wlayer[3]['temp']
            '''
            # Set visibility
            if self.conf.set_visibility and wlayer[3]['vis']:
                if prevlayer and prevlayer[0] != wlayer[0] and wlayer[3]['vis']:
                    self.visibility.value = c.interpolate(prevlayer[3]['vis'], wlayer[3]['vis'], prevlayer[0], wlayer[0], self.alt)
                else:
                    self.visibility.value = wlayer[3]['vis']
            '''
            # First wind level
            if self.conf.vatsim:
                return
            '''
            if not self.conf.use_metar:
                # Set first wind level if we don't use metar
                self.setWindLayer(wl[0], 0, winds[0], elapsed)
            elif self.alt > winds[0][0]:
                # Set first wind level on "descent"
                self.setWindLayer(wl[0], 0, winds[0], elapsed)
            '''
            
            if 'metar' in self.weatherData and 'wind' in self.weatherData['metar']:
                alt = self.weatherData['metar']['elevation'] 
                hdg, speed, gust = self.weatherData['metar']['wind']
                self.setWindLayer(wl[0], 0, [alt, hdg, speed, {'gust': gust}], elapsed)
                
    
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
    
    def setPressure(self, pressure, elapsed):
        # Transition
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
        
        #self.gfs = False
         
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
        
        y -= 35
        # Save
        self.saveButton = XPCreateWidget(x+25, y-20, x+125, y-60, 1, "Apply & Save", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.saveButton, xpProperty_ButtonType, xpPushButton)
        
        x += 170
        y = top
        
        aboutlines = 14
        
        # ABOUT/ STATUS Sub Window
        subw = XPCreateWidget(x+10, y-30, x2-20 + 10, y - (20 * aboutlines), 1, "" ,  0,window, xpWidgetClass_SubWindow)
        # Set the style to sub window
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        x += 20
        y -= 20
        
        # Add Close Box decorations to the Main Widget
        XPSetWidgetProperty(window, xpProperty_MainWindowHasCloseBoxes, 1)
        
        # Create status captions
        self.statusBuff = []
        for i in range(aboutlines):
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
        x += 80
        # Set the style to sub window
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        sysinfo = [
        'X-Plane NOAA Weather: %s' % self.conf.__VERSION__,
        '(c) joan perez cauhe 2012-15',
        ]
        for label in sysinfo:
            y -= 10
            XPCreateWidget(x, y, x+40, y-20, 1, label, 0, window, xpWidgetClass_Caption)
        
        y -= 20
        # Visit site Button
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
                self.conf.use_metar     = XPGetWidgetProperty(self.metarCheck, xpProperty_ButtonState, None)
                self.conf.vatsim        = XPGetWidgetProperty(self.vatsimCheck, xpProperty_ButtonState, None)
                self.conf.download      = XPGetWidgetProperty(self.downloadCheck, xpProperty_ButtonState, None)
                
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
                           'lat: %.2f/%.2f lon: %.2f/%.2f' % (self.weather.lat , wdata['info']['lat'], self.weather.lon, wdata['info']['lon']),
                           'GFS Cycle: %s' % (wdata['info']['gfs_cycle']),
                           'WAFS Cycle: %s' % (wdata['info']['wafs_cycle']),
                ]
        
            if 'metar' in wdata and 'icao' in wdata['metar']:
                sysinfo += [
                            'Metar station: %s %s' % (wdata['metar']['icao'], wdata['metar']['metar']),
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
                     
            if 'gfs' in wdata and 'winds' in wdata['gfs']:
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
                #if 'pressure' in wdata['gfs']:
                #    sysinfo += ['Pressure (gfs): %.2f' % (wdata['gfs']['pressure'])]
            
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

    def floopCallback(self, elapsedMe, elapsedSim, counter, refcon):
        '''
        Floop Callback
        '''
        
        '''
        # Switch METAR/GFS mode
        if self.conf.use_metar:
            if self.weather.xpWeatherOn.value == 1:
                if self.weather.alt > self.conf.transalt:
                    #  Swicth to GFS
                    self.weather.winds[0]['alt'].value = self.conf.transalt 
                    self.weather.ref_winds[0] = (self.weather.winds[1]['alt'].value, self.weather.winds[1]['hdg'].value, self.weather.winds[1]['speed'].value)
                    self.weather.xpWeatherOn.value = 0
                else:
                    return -1
            else:
                if self.weather.alt < self.conf.transalt:
                    # Switch to METAR
                    self.weather.winds[0]['alt'].value = self.conf.transalt 
                    self.weather.xpWeatherOn.value = 1
                    return -1
        '''
        
        if self.aboutWindow and XPIsWidgetVisible(self.aboutWindowWidget):
            self.updateStatus()
        
        if not self.conf.enabled or not self.weather.weatherData:
            # Worker stoped wait 4s
            return 4
        
        # Get weather lock
        #self.weather.lock.acquire()
        
        # get acf position
        self.weather.lat = self.latdr.value
        self.weather.lon = self.londr.value
        self.weather.alt = self.altdr.value
        
        wdata = self.weather.weatherData
        
        # Release lock
        #self.weather.lock.release()
        
        pressSet = False
        
        rain, ts = 0, 0
        
        # Set visibility from metar
        if 'metar' in wdata:
            if 'visibility' in wdata['metar']:
                self.weather.visibility.value =  c.limit(wdata['metar']['visibility'], self.conf.max_visibility)
            if 'pressure' in wdata['metar']:
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
            if self.conf.set_clouds and 'clouds' in wdata['gfs']:
                self.weather.setClouds(wdata['gfs']['clouds'])
            # Set pressure
            if not pressSet and self.conf.set_pressure and 'pressure' in wdata['gfs']:
                self.weather.setPressure(wdata['gfs']['pressure'], elapsedMe)
        
        if self.conf.set_turb and 'wafs' in wdata:
            self.weather.setTurbulence(wdata['wafs'])
            
        
        
        return -1
    
    def XPluginStop(self):
        # kill weather server/client
        self.weather.shutdown()
        
        # Destroy windows
        if self.aboutWindow:
            XPDestroyWidget(self, self.aboutWindowWidget, 1)
        XPLMUnregisterFlightLoopCallback(self, self.floop, 0)
        
        XPLMDestroyMenu(self, self.mMain)
        self.conf.save()
        
    def XPluginEnable(self):
        return 1
    
    def XPluginDisable(self):
        pass
    
    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        if (inParam == XPLM_PLUGIN_XPLANE and inMessage == XPLM_MSG_AIRPORT_LOADED):
            self.weather.startWeatherClient()
