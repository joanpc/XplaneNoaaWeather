'''
X-plane NOAA GFS weather plugin.

Sets x-plane wind and cloud layers using NOAA real/forecast data.
This plugin downloads required data from NOAA servers.

Uses wgrib2 to parse NOAA grib2 data files.
Includes wgrib2 binaries for Mac Win32 and linux i386glibc6
Win32 wgrib2 requires cgywin now included in the resources folder

TODO:
- Remove shear on transition
- remove old grib files from cache

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
                            # XP10 'coverage': EasyDref('"sim/weather/cloud_coverage[%d]"' % (i), 'float'),
                                })
            
        self.windata = []

        self.xpWeatherOn = EasyDref('sim/weather/use_real_weather_bool', 'int')
        self.msltemp     = EasyDref('sim/weather/temperature_sealevel_c', 'float')
        self.dewpoint    = EasyDref('sim/weather/dewpoi_sealevel_c', 'float')
        self.thermalAlt  = EasyDref('sim/weather/thermal_altitude_msl_m', 'float')
        self.visibility  = EasyDref('sim/weather/visibility_reported_m', 'float')
        self.pressure    = EasyDref('sim/weather/barometer_sealevel_inhg', 'float')
        
        
        # Data
        self.weatherData = False
        
        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.die = threading.Event()
        self.lock = threading.Lock()
        
        self.startWeatherServer()
        
        self.weatherClientThread = threading.Thread(target = self.weatherClient)
        self.weatherClientThread.start()
            
    def weatherClient(self):
        '''
        Wheather client thread fetches wheather from the server
        '''
        while not self.die.wait(self.conf.parserate):
            
            lat, lon = round(self.lat, 2), round(self.lon, 2)
            
            #TODO: time refresh/push
            if True or (self.last_lat, self.last_lon) != (lat, lon):
                
                self.last_lat, self.last_lon = lat, lon
                self.sock.sendto("?%f|%f\n" % (lat, lon), ('127.0.0.1', self.conf.server_port))
                received = self.sock.recv(1024*8)
            
                self.lock.acquire() 
                self.weatherData = cPickle.loads(received)
                self.lock.release()
    
    def startWeatherServer(self):
        DETACHED_PROCESS = 0x00000008
        args = [self.conf.pythonpath, self.conf.respath + '/weatherServer.py', self.conf.syspath]
        print args
        
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
        calt = xpwind['alt'].value

        if int(alt*100) != int(calt*100):
            # layer change trasition not needed xplane does interpolation
            xpwind['alt'].value, xpwind['hdg'].value, xpwind['speed'].value = alt, hdg, speed
            self.ref_winds[layer] = (alt, hdg, speed)
            
        else:
            # Transition
            if not layer in self.ref_winds:
                # Store reference wind layer to ignore x-plane roundings
                self.ref_winds[layer] = ( xpwind['alt'].value, xpwind['hdg'].value, xpwind['speed'].value)
            
            if self.ref_winds[layer] == (data[0], data[1], data[2]):
                # No need to transition if the data is updated
                return
            
            calt, chdg, cspeed = self.ref_winds[layer]
            hdg     = self.transHdg(chdg, hdg, elapsed)
            speed   = c.timeTrasition(cspeed, speed, elapsed)
            
            self.ref_winds[layer] = calt, hdg, speed
            xpwind['hdg'].value   = hdg
            xpwind['speed'].value = speed
        
    def transHdg(self, current, new, elapsed, vel=12):
        '''
        Time based wind heading transition
        '''
        diff = c.shortHdg(current, new)
        if abs(diff) < vel*elapsed:
            return new
        else:
            if diff > 0:
                diff = +1
            else:
                diff = -1
            newval = current + diff * vel * elapsed
            if newval < 0:
                return newval + 360
            else:
                return newval % 360
        
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
            
            if not self.conf.use_metar:
                # Set first wind level if we don't use metar
                self.setWindLayer(wl[0], 0, winds[0], elapsed)
            elif self.alt > winds[0][0]:
                # Set first wind level on "descent"
                self.setWindLayer(wl[0], 0, winds[0], elapsed)
    
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
        if not 'ref_pressure' in self.__dict__:
            self.ref_pressure = self.pressure.value 
        self.pressure.value = c.timeTrasition(self.ref_pressure, pressure, elapsed, 0.1)
    
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
                #if not self.gfs:
                #    self.gfs = GFS(self.conf)
                #    self.gfs.start()
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
        '''
        if self.gfs:
            
            if self.gfs.lastgrib:
                lastgrib = self.gfs.lastgrib.split('/')

                sysinfo = [
                'XPGFS Status:',
                'lat: %.2f/%.1f lon: %.2f/%.1f' % (self.gfs.lat, self.gfs.parsed_latlon[0], self.gfs.lon, self.gfs.parsed_latlon[1]),
                'GFS Cycle: %s' % (lastgrib[0]),
                'cloud layers: %i' % (self.gfs.nclouds),
                ]
                
            if self.gfs.winds:
                sysinfo += ['Wind layers: %i FL/HDG/KT' % (self.gfs.nwinds)]
                wlayers = ''
                i = 0
                for layer in self.gfs.winds:
                    i += 1
                    alt, hdg, speed = layer[0], layer[1], layer[2]
                    wlayers += 'FL%d/%03d/%d ' % (alt * 3.28084 / 100, hdg, speed)
                    if i > 5:
                        i = 0
                        sysinfo += [wlayers]
                        wlayers = ''    
                if i > 0:
                    sysinfo += [wlayers]  
                

            if self.gfs.wafs.lastgrib:
                lastwafsgrib = self.gfs.wafs.lastgrib.split('/')
                
                tblayers = ''
                for layer in self.gfs.wafs.turbulence:
                    tblayers += 'FL%d/%.1f ' % (layer[0] * 3.28084 / 100, layer[1]) 
                
                sysinfo += [
                            'WAFS Cycle: %s' % (lastwafsgrib[0]),
                            'Turbulence layers: %i' % (self.gfs.wafs.nturbulence),
                            tblayers
                            ]
                
            if self.gfs.metar.weather:
                sysinfo += [
                            'Metar station: %s %s' % (self.gfs.metar.weather['icao'], self.gfs.metar.weather['metar']),
                            'Temperature: %.1f, Dewpoint: %.1f, ' % (self.gfs.metar.weather['temperature'][0], self.gfs.metar.weather['temperature'][1]) +
                            'Visibility: %d meters, ' % (self.gfs.metar.weather['visibility']) +
                            'Pressure: %f inhg ' % (self.gfs.metar.weather['pressure']),
                            #'Wind speed: %dkt, gust +%dkt'  (self.gfs.metar.weather['wind'][0], self.gfs.metar.weather['wind'][1])
                           ]
            if self.gfs.downloading:
                sysinfo.append('Downloading new cycle.')              
                
                
        else:
            sysinfo = ['XPGFS Status:',
                       'Data not ready'
                       ]
        '''
        
        sysinfo = []
        
        if not self.weather.weatherData:
            sysinfo = ['Data not ready. Please wait.']
        
        else:
            wdata = self.weather.weatherData
            if 'info' in wdata:
                sysinfo = [
                           'XPGFS Status:',
                           'lat: %.2f/%.1f lon: %.2f/%.1f' % (self.weather.lat , wdata['info']['lat'], self.weather.lon, wdata['info']['lon']),
                           'GFS Cycle: %s' % (wdata['info']['gfs_cycle']),
                           'WAFS Cycle: %s' % (wdata['info']['wafs_cycle']),
                ]
        
            if 'metar' in wdata:
                sysinfo += [
                            'Metar station: %s %s' % (wdata['metar']['icao'], wdata['metar']['metar']),
                            'Temperature: %.1f, Dewpoint: %.1f, ' % (wdata['metar']['temperature'][0], wdata['metar']['temperature'][1]) +
                            'Visibility: %d meters, ' % (wdata['metar']['visibility']) +
                            'Pressure: %f inhg ' % (wdata['metar']['pressure']),
                            'Wind speed: %dkt, gust +%dkt' % (wdata['metar']['wind'][0], wdata['metar']['wind'][1])
                           ]
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
        self.weather.lock.acquire()
        
        # get acf position
        self.weather.lat = self.latdr.value
        self.weather.lon = self.londr.value
        self.weather.alt = self.altdr.value
        
        wdata = self.weather.weatherData
        
        # Release lock
        self.weather.lock.release()
        
        # Set visibility from metar
        if 'metar' in wdata:
            if wdata['metar']['visibility']:
                self.weather.visibility.value =  c.limit(wdata['metar']['visibility'], self.conf.max_visibility)
        
        if 'gfs' in wdata:    
            # Set winds and clouds
            if self.conf.set_wind and 'winds' in wdata['gfs']:
                self.weather.setWinds(wdata['gfs']['winds'], elapsedMe)
            if self.conf.set_clouds and 'clouds' in wdata['gfs']:
                self.weather.setClouds(wdata['gfs']['clouds'])
            # Set pressure
            if self.conf.set_pressure and 'pressure' in wdata['gfs']:
                self.weather.setPressure(wdata['gfs']['pressure'], elapsedSim)
        
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
            # X-Plane loaded, start worker
            #if not self.gfs:
            #    self.gfs = GFS(self.conf)
            #    self.gfs.start()
            pass
