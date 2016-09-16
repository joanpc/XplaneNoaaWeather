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

import ctypes
import cPickle
import socket
import threading
import subprocess
import os
import signal
from datetime import datetime
from random import random

from noaweather import EasyDref, Conf, c, EasyCommand

class Weather:
    '''
    Sets x-plane weather from GSF parsed data
    '''
    alt = 0.0
    ref_winds = {}
    lat, lon, last_lat, last_lon = 99, 99, False, False

    def __init__(self, conf, data):

        self.conf = conf
        self.data = data
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
        self.runwayFriction = EasyDref('sim/weather/runway_friction', 'float')

        self.mag_deviation = EasyDref('sim/flightmodel/position/magnetic_variation', 'float')

        self.acf_vy = EasyDref('sim/flightmodel/position/local_vy', 'float')

        # Data
        self.weatherData = False
        self.weatherClientThread = False

        self.windAlts = -1

        # Response queue for user queries
        self.queryResponses = []

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
        self.weatherClientSend('!ping')

        while True:
            received = self.sock.recv(1024*8)
            wdata = cPickle.loads(received)
            if self.die.is_set() or wdata == '!bye':
                break
            elif not 'info' in wdata:
                # A metar query response
                self.queryResponses.append(wdata)
            else:
                self.weatherData = wdata
                self.newData = True

    def weatherClientSend(self, msg):
        if self.weatherClientThread:
            self.sock.sendto(msg,('127.0.0.1', self.conf.server_port))

    def startWeatherServer(self):
        DETACHED_PROCESS = 0x00000008
        args = [self.conf.pythonpath, os.sep.join([self.conf.respath, 'weatherServer.py']), self.conf.syspath]

        if self.conf.spinfo:
            p = subprocess.Popen(args, startupinfo=self.conf.spinfo, close_fds=True, creationflags=DETACHED_PROCESS)
        else:
            p = subprocess.Popen(args, close_fds=True)

    def shutdown(self):
        # Shutdown client and server
        self.weatherClientSend('!shutdown')
        self.weatherClientThread = False


    def setTurbulence(self, turbulence, elapsed):
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
        turb *= self.conf.turbulence_probability
        turb = c.randPattern('turbulence', turb, elapsed, 20, min_time = 1)

        self.winds[0]['turbulence'].value = turb
        self.winds[1]['turbulence'].value = turb
        self.winds[2]['turbulence'].value = turb

    def setWinds(self, winds, elapsed):
        '''Set winds: Interpolate layers and transition new data'''

        winds = winds[:]

        # Append metar layer
        if 'metar' in self.weatherData and 'wind' in self.weatherData['metar']:
            alt = self.weatherData['metar']['elevation']
            hdg, speed, gust = self.weatherData['metar']['wind']
            extra = {'gust': gust, 'metar': True}

            if 'variable_wind' in self.weatherData['metar'] and self.weatherData['metar']['variable_wind']:
                h1, h2 = self.weatherData['metar']['variable_wind']
                extra['variation'] = hdg - c.randPattern('metar_wind_hdg', h1, elapsed, min_val = h2, min_time = 20, max_time = 50, heading = True)

            alt += self.conf.metar_agl_limit
            alt = c.transition(alt, '0-metar_wind_alt', elapsed, 0.3048) # 1f/s

            # Fix temperatures
            if 'temperature' in self.weatherData['metar']:
                if self.weatherData['metar']['temperature'][0] is not False:
                    extra['temp'] = self.weatherData['metar']['temperature'][0] + 273.15
                if self.weatherData['metar']['temperature'][1] is not False:
                    extra['dew'] = self.weatherData['metar']['temperature'][1] + 273.15

            # remove first wind layer if is too close (for high altitude airports)
            # TODO: This can break transitions in some cases.
            if len(winds) > 1 and winds[0][0] < alt+ self.conf.metar_agl_limit:
                winds.pop(0)

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
                rwind = self.interpolateWindLayer(twind, bwind, self.alt, blayer)

            else:
                # We are below the first layer or above the last one.
                rwind = twind;

            # Set layers
            self.setWindLayer(0, rwind, elapsed)
            self.setWindLayer(1, rwind, elapsed)
            self.setWindLayer(2, rwind, elapsed)

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

    def setWindLayer(self, index,  wlayer, elapsed):
        alt, hdg, speed, extra = wlayer

        wind = self.winds[index]

        if 'variation' in extra:
            hdg += extra['variation']

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
        ''' Set a dateref if the current value differs
            Returns if value was set '''

        if max_diff is not False:
            if abs(dref.value - value) > max_diff:
                dref.value = value
                return True
        else:
            if dref.value != value:
                dref.value = value
                return True
        return False

    def interpolateWindLayer(self, wlayer1, wlayer2, current_altitude, nlayer = 1):
        ''' Interpolates 2 wind layers
        layer array: [alt, hdg, speed, extra] '''

        if wlayer1[0] == wlayer2[0]:
            return wlayer1

        layer = [0, 0, 0, {}]

        layer[0] = current_altitude
        if nlayer:
            layer[1] = c.interpolateHeading(wlayer1[1], wlayer2[1], wlayer1[0], wlayer2[0], current_altitude)
            layer[2] = c.interpolate(wlayer1[2], wlayer2[2], wlayer1[0], wlayer2[0], current_altitude)
        else:
            # First layer
            layer[1] = c.expoCosineInterpolateHeading(wlayer1[1], wlayer2[1], wlayer1[0], wlayer2[0], current_altitude)
            layer[2] = c.expoCosineInterpolate(wlayer1[2], wlayer2[2], wlayer1[0], wlayer2[0], current_altitude)


        if not 'variation' in wlayer1[3]:
            wlayer1[3]['variation'] = 0

        # Interpolate extras
        for key in wlayer1[3]:
            if key in wlayer2[3] and wlayer2[3][key] is not False:
                if nlayer:
                    layer[3][key] = c.interpolate(wlayer1[3][key], wlayer2[3][key], wlayer1[0], wlayer2[0], current_altitude)
                else:
                    layer[3][key] = c.expoCosineInterpolate(wlayer1[3][key], wlayer2[3][key], wlayer1[0], wlayer2[0], current_altitude)
            else:
                # Leave null temp and dew if we can't interpolate
                if key not in ('temp', 'dew'):
                    layer[3][key] = wlayer1[3][key]

        return layer

    def setClouds(self):

        if 'clouds' in self.weatherData['gfs']:
            gfsClouds = self.weatherData['gfs']['clouds']
        else:
            gfsClouds = []

        # X-Plane cloud limits
        minCloud = c.f2m(2000)
        maxCloud = c.f2m(c.limit(40000, self.conf.max_cloud_height))

        # Minimum redraw difference per layer
        minRedraw = [c.f2m(500), c.f2m(5000), c.f2m(10000)]

        xpClouds = {
                    'FEW': [1, c.f2m(2000)],
                    'SCT': [2, c.f2m(4000)],
                    'BKN': [3, c.f2m(4000)],
                    'OVC': [4, c.f2m(4000)],
                    'VV': [4, c.f2m(6000)]
                    }

        lastBase = 0
        maxTop = 0
        gfsCloudLimit = c.f2m(5600)

        setClouds = []

        if self.weatherData and 'distance' in self.weatherData['metar'] and self.weatherData['metar']['distance'] < self.conf.metar_distance_limit and 'clouds' in self.weatherData['metar']:

            clouds =  self.weatherData['metar']['clouds'][:]

            gfsCloudLimit += self.weatherData['metar']['elevation']

            for cloud in reversed(clouds):
                base, cover, extra = cloud
                top = minCloud

                if cover in xpClouds:
                    top = base + xpClouds[cover][1]
                    cover = xpClouds[cover][0]

                # Search for gfs equivalent layer
                for gfsCloud in gfsClouds:
                    gfsBase, gfsTop, gfsCover = gfsCloud

                    if gfsBase > 0 and gfsBase - 1500 < base < gfsTop:
                        top = base + c.limit(gfsTop - gfsBase, maxCloud, minCloud)
                        break

                if lastBase and top > lastBase: top = lastBase
                lastBase = base

                setClouds.append([base, top, cover])

                if not maxTop:
                    maxTop = top

            # add gfs clouds
            for cloud in gfsClouds:
                base, top, cover = cloud

                if len(setClouds) < 3 and base > max(gfsCloudLimit, maxTop):
                    cover = c.cc2xp(cover)

                    top = base + c.limit(top - base, maxCloud, minCloud)
                    setClouds = [[base, top, cover]] + setClouds

        else:
            # GFS-only clouds
            for cloud in reversed(gfsClouds):
                base, top, cover = cloud
                cover = c.cc2xp(cover)

                if cover > 0 and base > 0 and top > 0:
                    if cover < 3:
                        top = base + minCloud
                    else:
                        top = base + c.limit(top - base, maxCloud, minCloud)

                    if lastBase > top: top = lastBase
                    setClouds.append([base, top, cover])
                    lastBase = base

        # Set the Cloud to Datarefs
        redraw = 0
        nClouds = len(setClouds)
        setClouds = list(reversed(setClouds))

        # Push up gfs clouds to prevent redraws
        if nClouds:
            if nClouds < 3 and setClouds[0][0] > gfsCloudLimit:
                setClouds = [[0, minCloud, 0]] + setClouds
            if 1 < len(setClouds) < 3 and setClouds[1][2] > gfsCloudLimit:
                setClouds = [setClouds[0], [setClouds[0][2], setClouds[0][2] + minCloud, 0 ] , setClouds[1]]

        nClouds = len(setClouds)

        if not self.data.override_clouds:
            for i in range(3):
                if nClouds > i:
                    base, top, cover = setClouds[i]
                    redraw += self.setDrefIfDiff(self.clouds[i]['bottom'], base, minRedraw[i] + self.alt/10)
                    redraw += self.setDrefIfDiff(self.clouds[i]['top'], top, minRedraw[i] + self.alt/10)
                    redraw += self.setDrefIfDiff(self.clouds[i]['coverage'], cover, 1)
                else:
                    redraw += self.setDrefIfDiff(self.clouds[i]['coverage'], 0)

        # Update datarefs
        bases = []
        tops = []
        covers = []

        for layer in setClouds:
            base, top, cover = layer
            bases.append(base)
            tops.append(top)
            covers.append(cover)

        self.data.cloud_base.value = bases
        self.data.cloud_top.value = tops
        self.data.cloud_cover.value = covers

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

class Data:
    '''
    Plugin dataref data publishing
    '''

    def __init__(self, plugin):

        EasyDref.plugin = plugin
        self.registered = False
        self.registerTries = 0

        # Overrides
        self.override_clouds = EasyDref('xjpc/XPNoaaWeather/config/override_clouds', 'int', register = True, writable = True);
        self.override_winds = EasyDref('xjpc/XPNoaaWeather/config/override_winds', 'int', register = True, writable = True)
        self.override_visibility = EasyDref('xjpc/XPNoaaWeather/config/override_visibility', 'int', register = True, writable = True)
        self.override_turbulence = EasyDref('xjpc/XPNoaaWeather/config/override_turbulence', 'int', register = True, writable = True)
        self.override_pressure = EasyDref('xjpc/XPNoaaWeather/config/override_pressure', 'int', register = True, writable = True)
        self.override_precipitation = EasyDref('xjpc/XPNoaaWeather/config/override_precipitation', 'int', register = True, writable = True)
        self.override_runway_friction = EasyDref('xjpc/XPNoaaWeather/config/override_runway_friction', 'int', register = True, writable = True)

        # Weather variables
        self.ready = EasyDref('xjpc/XPNoaaWeather/weather/ready', 'float', register = True)
        self.visibility = EasyDref('xjpc/XPNoaaWeather/weather/visibility', 'float', register = True)

        self.nwinds = EasyDref('xjpc/XPNoaaWeather/weather/gfs_nwinds', 'int', register = True)
        self.wind_alt = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_alt[16]', 'float', register = True)
        self.wind_hdg = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_hdg[16]', 'float', register = True)
        self.wind_speed = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_speed[16]', 'float', register = True)
        self.wind_temp = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_temp[16]', 'float', register = True)

        self.cloud_base = EasyDref('xjpc/XPNoaaWeather/weather/cloud_base[3]', 'float', register = True)
        self.cloud_top = EasyDref('xjpc/XPNoaaWeather/weather/cloud_top[3]', 'float', register = True)
        self.cloud_cover = EasyDref('xjpc/XPNoaaWeather/weather/cloud_cover[3]', 'float', register = True)

        self.nturbulence =  EasyDref('xjpc/XPNoaaWeather/weather/wafs_nturb', 'int', register = True)
        self.turbulence_alt = EasyDref('xjpc/XPNoaaWeather/weather/turbulence_alt[16]', 'float', register = True)
        self.turbulence_sev = EasyDref('xjpc/XPNoaaWeather/weather/turbulence_sev[16]', 'float', register = True)

        # Metar variables
        self.metar_temperature = EasyDref('xjpc/XPNoaaWeather/weather/metar_temperature', 'float', register = True)
        self.metar_dewpoint = EasyDref('xjpc/XPNoaaWeather/weather/metar_dewpoint', 'float', register = True)
        self.metar_pressure = EasyDref('xjpc/XPNoaaWeather/weather/metar_pressure', 'float', register = True)
        self.metar_visibility = EasyDref('xjpc/XPNoaaWeather/weather/metar_visibility', 'float', register = True)
        self.metar_precipitation = EasyDref('xjpc/XPNoaaWeather/weather/metar_precipitation', 'int', register = True)
        self.metar_thunderstorm = EasyDref('xjpc/XPNoaaWeather/weather/metar_thunderstorm', 'int', register = True)
        self.metar_runwayFriction = EasyDref('xjpc/XPNoaaWeather/weather/metar_runwayFriction', 'float', register = True)

    def updateData(self, wdata):
        '''Publish raw dataref data
        some data is published elsewhere
        '''

        if not self.registered:
            self.registered = EasyDref.DataRefEditorRegister()
            self.registerTries += 1
            if self.registerTries > 20:
                self.registered = True

        if not wdata:
            self.ready.value = 0
        else:
            self.ready.value = 1
            if 'metar' in wdata and 'icao' in wdata['metar']:

                self.metar_temperature.value = wdata['metar']['temperature'][0]
                self.metar_dewpoint.value = wdata['metar']['temperature'][1]
                self.metar_pressure.value = wdata['metar']['pressure']
                self.metar_visibility.value = wdata['metar']['visibility']

            if 'gfs' in wdata:
                if 'winds' in wdata['gfs']:

                    alts = []
                    hdgs = []
                    speeds = []
                    temps = []

                    for layer in wdata['gfs']['winds']:
                        alt, hdg, speed, extra = layer
                        alts.append(alt)
                        hdgs.append(hdg)
                        speeds.append(speed)
                        temps.append(extra['temp'])

                    self.nwinds = len(alts)
                    self.wind_alt.value = alts
                    self.wind_hdg.value = hdgs
                    self.wind_speed.value = speeds
                    self.wind_temp.value = temps

            if 'wafs' in wdata:

                turb_fl = []
                turb_sev = []
                for layer in wdata['wafs']:
                    turb_fl.append(layer[0])
                    turb_fl.append(layer[1])

                self.nturbulence = len(turb_fl)
                self.turbulence_alt = turb_fl
                self.turbulence_sev = turb_sev

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

        self.data = Data(self)
        self.weather = Weather(self.conf, self.data)

        # floop
        self.floop = self.floopCallback
        XPLMRegisterFlightLoopCallback(self, self.floop, -1, 0)

        # Menu / About
        self.Mmenu = self.mainMenuCB
        self.aboutWindow = False
        self.metarWindow = False
        self.mPluginItem = XPLMAppendMenuItem(XPLMFindPluginsMenu(), 'XP NOAA Weather', 0, 1)
        self.mMain       = XPLMCreateMenu(self, 'XP NOAA Weather', XPLMFindPluginsMenu(), self.mPluginItem, self.Mmenu, 0)

        # Menu Items
        XPLMAppendMenuItem(self.mMain, 'Configuration', 1, 1)
        XPLMAppendMenuItem(self.mMain, 'Metar Query', 2, 1)

        # Register commands
        self.metarWindowCMD = EasyCommand(self, 'metar_query_window_toggle',self.metarQueryWindowToggle, description="Toggle METAR query window.")

        # Flightloop counters
        self.flcounter = 0
        self.fltime = 1
        self.lastParse = 0

        self.newAptLoaded = False

        self.aboutlines = 17

        return self.Name, self.Sig, self.Desc

    def mainMenuCB(self, menuRef, menuItem):
        '''
        Main menu Callback
        '''
        if menuItem == 1:
            if not self.aboutWindow:
                self.CreateAboutWindow(221, 640)
                self.aboutWindow = True
            elif (not XPIsWidgetVisible(self.aboutWindowWidget)):
                XPShowWidget(self.aboutWindowWidget)

        elif menuItem == 2:
            if not self.metarWindow:
                self.createMetarWindow()
            elif not XPIsWidgetVisible(self.metarWindowWidget):
                XPShowWidget(self.metarWindowWidget)
                XPSetKeyboardFocus(self.metarQueryInput)


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
        y -= 28
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


        y -= 25
        self.turbulenceCaption = XPCreateWidget(x, y-40, x+80, y-60, 1, 'Turbulence probability %d%%' % (self.conf.turbulence_probability * 100), 0, window, xpWidgetClass_Caption)
        y -= 20
        self.turbulenceSlider = XPCreateWidget(x+5, y-40, x+160, y-60, 1, '', 0, window, xpWidgetClass_ScrollBar)
        XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarType, xpScrollBarTypeSlider)
        XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarMin, 10)
        XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarMax, 1000)
        XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarPageAmount, 1)

        XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarSliderPosition, int(self.conf.turbulence_probability * 1000))

        y -= 25
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

        y -= 40
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

        sysinfo = [
        'X-Plane NOAA Weather: %s' % self.conf.__VERSION__,
        '(c) joan perez i cauhe 2012-15',
        ]
        for label in sysinfo:
            XPCreateWidget(x, y-5, x+120, y-20, 1, label, 0, window, xpWidgetClass_Caption)
            y -= 15

        # Visit site Button
        x += 190
        y += 15
        self.aboutVisit = XPCreateWidget(x, y, x+100, y-20, 1, "Official site", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.aboutVisit, xpProperty_ButtonType, xpPushButton)

        self.aboutForum = XPCreateWidget(x+120, y, x+220, y-20, 1, "Support", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.aboutForum, xpProperty_ButtonType, xpPushButton)

        # Donate Button
        self.donate = XPCreateWidget(x+240, y, x+340, y-20, 1, "Donate", 0, window, xpWidgetClass_Button)
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

        if inMessage == xpMsg_ScrollBarSliderPositionChanged and inParam1 == self.turbulenceSlider:
            val = XPGetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarSliderPosition, None)
            XPSetWidgetDescriptor(self.turbulenceCaption, 'Turbulence probability %d%%' % (val/10))
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
            if (inParam1 == self.aboutForum):
                from webbrowser import open_new
                open_new('http://forums.x-plane.org/index.php?showtopic=72313&view=getnewpost');
                return 1
            if inParam1 == self.saveButton:
                # Save configuration
                self.conf.enabled       = XPGetWidgetProperty(self.enableCheck, xpProperty_ButtonState, None)
                self.conf.set_wind      = XPGetWidgetProperty(self.windsCheck, xpProperty_ButtonState, None)
                self.conf.set_clouds    = XPGetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, None)
                self.conf.set_temp      = XPGetWidgetProperty(self.tempCheck, xpProperty_ButtonState, None)
                self.conf.set_pressure  = XPGetWidgetProperty(self.pressureCheck, xpProperty_ButtonState, None)
                self.conf.turbulence_probability = XPGetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarSliderPosition, None) / 1000.0

                # Zero turbulence data if disabled
                self.conf.set_turb      = XPGetWidgetProperty(self.turbCheck, xpProperty_ButtonState, None)
                if not self.conf.set_turb:
                    for i in range(3): self.weather.winds[i]['turbulence'].value = 0

                self.conf.download      = XPGetWidgetProperty(self.downloadCheck, xpProperty_ButtonState, None)

                #buff = []
                #XPGetWidgetDescriptor(self.transAltInput, buff, 256)
                #self.conf.metar_agl_limit = c.convertFromInput(buff[0], 'f2m', 900)

                buff = []
                XPGetWidgetDescriptor(self.maxCloudHeightInput, buff, 256)
                self.conf.max_cloud_height = c.convertFromInput(buff[0], 'f2m', min = c.f2m(2000))

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

                # Reset things
                self.weather.newData = True
                self.newAptLoaded = True

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

        #XPSetWidgetDescriptor(self.transAltInput, c.convertForInput(self.conf.metar_agl_limit, 'm2ft'))
        XPSetWidgetDescriptor(self.maxVisInput, c.convertForInput(self.conf.max_visibility, 'm2sm'))
        XPSetWidgetDescriptor(self.maxCloudHeightInput, c.convertForInput(self.conf.max_cloud_height, 'm2ft'))

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
                            '    Apt altitude: %dft, Apt distance: %.1fkm' % (wdata['metar']['elevation'] * 3.28084, wdata['metar']['distance']/1000),
                            '    Temp: %s, Dewpoint: %s, ' % (c.strFloat(wdata['metar']['temperature'][0]), c.strFloat(wdata['metar']['temperature'][1])) +
                            'Visibility: %d m, ' % (wdata['metar']['visibility']) +
                            'Press: %s inhg ' % (c.strFloat(wdata['metar']['pressure']))
                            ]

                wind = '    Wind:  %d %dkt, gust +%dkt' % (wdata['metar']['wind'][0], wdata['metar']['wind'][1], wdata['metar']['wind'][2])
                if 'variable_wind' in wdata['metar'] and wdata['metar']['variable_wind']:
                    wind += '   Variable: %d-%d' % (wdata['metar']['variable_wind'][0], wdata['metar']['variable_wind'][1])

                sysinfo += [wind]
                if 'precipitation' in wdata['metar'] and len(wdata['metar']['precipitation']):
                    precip = ''
                    for type in wdata['metar']['precipitation']:
                        if wdata['metar']['precipitation'][type]['recent']:
                            precip += wdata['metar']['precipitation'][type]['recent']
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
                    for layer in wdata['gfs']['clouds']:
                        top, bottom, cover = layer
                        if top > 0:
                            clouds += '   %03d|%03d|%d%% ' % (top * 3.28084/100, bottom * 3.28084/100, cover)
                    sysinfo += [clouds]

            if 'wafs' in wdata:
                tblayers = ''
                for layer in wdata['wafs']:
                    tblayers += '   %03d|%.1f ' % (layer[0] * 3.28084 / 100, layer[1])

                sysinfo += ['WAFS TURBULENCE: FL|SEV %d' % (len(wdata['wafs'])), tblayers]

        sysinfo += ['--'] * (self.aboutlines - len(sysinfo))

        return sysinfo

    def createMetarWindow(self):
        x = 100
        w = 480
        y = 600
        h = 120
        x2 = x + w
        y2 = y - h
        windowTitle = "METAR Request"

        # Create the Main Widget window
        self.metarWindow = True
        self.metarWindowWidget = XPCreateWidget(x, y, x2, y2, 1, windowTitle, 1, 0, xpWidgetClass_MainWindow)
        XPSetWidgetProperty(self.metarWindowWidget, xpProperty_MainWindowType,  xpMainWindowStyle_Translucent)

        # Config Sub Window, style
        #subw = XPCreateWidget(x+10, y-30, x2-20 + 10, y2+40 -25, 1, "" ,  0,self.metarWindowWidget , xpWidgetClass_SubWindow)
        #XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        XPSetWidgetProperty(self.metarWindowWidget, xpProperty_MainWindowHasCloseBoxes, 1)
        x += 10
        y -= 20

        cap = XPCreateWidget(x, y, x+40, y-20, 1, 'Airport ICAO code:', 0, self.metarWindowWidget, xpWidgetClass_Caption)
        XPSetWidgetProperty(cap, xpProperty_CaptionLit, 1)


        y -= 20
        # Airport input
        self.metarQueryInput = XPCreateWidget(x+5, y, x+120, y-20, 1, "", 0, self.metarWindowWidget, xpWidgetClass_TextField)
        XPSetWidgetProperty(self.metarQueryInput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.metarQueryInput, xpProperty_Enabled, 1)
        XPSetWidgetProperty(self.metarQueryInput, xpProperty_TextFieldType, xpTextTranslucent)

        self.metarQueryButton = XPCreateWidget(x+140, y, x+210, y-20, 1, "Request", 0, self.metarWindowWidget, xpWidgetClass_Button)
        XPSetWidgetProperty(self.metarQueryButton, xpProperty_ButtonType, xpPushButton)
        XPSetWidgetProperty(self.metarQueryButton, xpProperty_Enabled, 1)

        y -= 20
        # Help caption
        cap = XPCreateWidget(x, y, x+300, y-20, 1, "METAR:", 0, self.metarWindowWidget, xpWidgetClass_Caption)
        XPSetWidgetProperty(cap, xpProperty_CaptionLit, 1)

        y -= 20
        # Query output
        self.metarQueryOutput = XPCreateWidget(x+5, y, x+450, y-20, 1, "", 0, self.metarWindowWidget, xpWidgetClass_TextField)
        XPSetWidgetProperty(self.metarQueryOutput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.metarQueryOutput, xpProperty_Enabled, 1)
        XPSetWidgetProperty(self.metarQueryOutput, xpProperty_TextFieldType, xpTextTranslucent)

        # Register our widget handler
        self.metarQueryInputHandlerCB = self.metarQueryInputHandler
        XPAddWidgetCallback(self, self.metarQueryInput, self.metarQueryInputHandlerCB)

        # Register our widget handler
        self.metarWindowHandlerCB = self.metarWindowHandler
        XPAddWidgetCallback(self, self.metarWindowWidget, self.metarWindowHandlerCB)


        XPSetKeyboardFocus(self.metarQueryInput)

    def metarQueryInputHandler(self, inMessage, inWidget, inParam1, inParam2):
        ''' Override texfield keyboard input to be more friendly'''
        if inMessage == xpMsg_KeyPress:

            if not self.conf.inputbug:
                try:
                    key, flags, vkey = PI_GetKeyState(inParam1)
                except:
                    # nasty bug, disable inputHandler on config
                    self.conf.inputbug = True
                    return 1
            else:
                key, flags, vkey = PI_GetKeyState(ctypes.c_uint32(inParam1).value)

            if flags == 8:
                buff = []
                cursor = XPGetWidgetProperty(self.metarQueryInput, xpProperty_EditFieldSelStart, None)
                XPGetWidgetDescriptor(self.metarQueryInput, buff, 256)
                text = buff[0]
                if key in (8, 127):
                    #pass
                    XPSetWidgetDescriptor(self.metarQueryInput, text[:-1])
                    cursor -= 1
                elif key == 13:
                    #Enter
                    self.metarQuery()
                elif key == 27:
                    #ESC
                    XPLoseKeyboardFocus(self.metarQueryInput)
                elif 65 <= key <= 90 or 97 <= key <= 122 and len(text) < 4:
                    text += chr(key).upper()
                    XPSetWidgetDescriptor(self.metarQueryInput, text)
                    cursor += 1

                ltext = len(text)
                if cursor < 0: cursor = 0
                if cursor > ltext: cursor = ltext

                XPSetWidgetProperty(self.metarQueryInput, xpProperty_EditFieldSelStart, cursor)
                XPSetWidgetProperty(self.metarQueryInput, xpProperty_EditFieldSelEnd, cursor)

                return 1
        elif inMessage in (xpMsg_MouseDrag, xpMsg_MouseDown, xpMsg_MouseUp):
            XPSetKeyboardFocus(self.metarQueryInput)
            return 1
        return 0

    def metarWindowHandler(self, inMessage, inWidget, inParam1, inParam2):
        if inMessage == xpMessage_CloseButtonPushed:
            if self.metarWindow:
                XPHideWidget(self.metarWindowWidget)
                return 1
        if inMessage == xpMsg_PushButtonPressed:
            if (inParam1 == self.metarQueryButton):
                self.metarQuery()
                return 1
        return 0

    def metarQuery(self):
        buff = []
        XPGetWidgetDescriptor(self.metarQueryInput, buff, 256)
        query = buff[0].strip()
        if len(query) == 4:
            self.weather.weatherClientSend('?' + query)
            XPSetWidgetDescriptor(self.metarQueryOutput, 'Quering, please wait.')
        else:
            XPSetWidgetDescriptor(self.metarQueryOutput, 'Please insert a valid ICAO code.')

    def metarQueryCallback(self, msg):
        ''' Callback for metar queries '''

        if self.metarWindow:
            # Filter metar text
            metar = filter(lambda x: x in self.conf.printableChars, msg['metar']['metar'])
            XPSetWidgetDescriptor(self.metarQueryOutput, '%s %s' % (msg['metar']['icao'], metar))

    def metarQueryWindowToggle(self):
        ''' Metar window toggle command '''
        if self.metarWindow:
            if XPIsWidgetVisible(self.metarWindowWidget):
                XPHideWidget(self.metarWindowWidget)
            else:
                XPShowWidget(self.metarWindowWidget)
        else:
            self.createMetarWindow()

    def dumpLog(self):
        ''' Dumps all the information to a file to report bugs'''

        dumpath = os.sep.join([self.conf.cachepath, 'dumplogs'])

        if not os.path.exists(dumpath):
            os.makedirs(dumpath)

        dumplog = os.sep.join([dumpath, datetime.utcnow().strftime('%Y%m%d_%H%M%SZdump.txt')])

        f = open(dumplog, 'w')

        import platform
        from pprint import pprint

        xpver, sdkver, hid = XPLMGetVersions()
        output = ['--- Platform Info ---\n',
                  'Plugin version: %s\n' % self.conf.__VERSION__,
                  'Xplane Version: %.3f, SDK Version: %.2f\n' % (xpver/1000.0, sdkver/100.0),
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

        # Append tail of PythonInterface log files
        logfiles = ['PythonInterfaceLog.txt',
                    'PythonInterfaceOutput.txt',
                    os.path.join('noaweather', 'weatherServerLog.txt'),
                    ]

        for logfile in logfiles:
            filepath = os.sep.join([self.conf.syspath, 'Resources', 'plugins', 'PythonScripts', logfile])
            if os.path.exists(filepath):

                lfsize = os.path.getsize(filepath)
                lf = open(filepath, 'r')
                lf.seek(c.limit(1024 * 6, lfsize) * -1 , 2)
                f.write('\n--- %s ---\n\n' % logfile)
                for line in lf.readlines():
                    f.write(line.strip('\r'))
                lf.close()

        f.close()

        return dumplog

    def floopCallback(self, elapsedMe, elapsedSim, counter, refcon):
        '''
        Floop Callback
        '''

        # Update status window
        if self.aboutWindow and XPIsWidgetVisible(self.aboutWindowWidget):
            self.updateStatus()

        # Handle server misc requests
        if len(self.weather.queryResponses):
            msg = self.weather.queryResponses.pop()
            if 'metar' in msg:
                self.metarQueryCallback(msg)

        ''' Return if the plugin is disabled '''
        if not self.conf.enabled:
            return -1

        ''' Request new data from the weather server (if required)'''
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

        # Store altitude
        self.weather.alt = self.altdr.value
        wdata = self.weather.weatherData

        ''' Return if there's no weather data'''
        if self.weather.weatherData is False:
            return -1

        ''' Data set on new weather Data '''
        if self.weather.newData:
            rain, ts, friction = 0, 0, 0

            # Clear transitions on airport load
            if self.newAptLoaded:
                c.transitionClearReferences()
                self.newAptLoaded = False

            # Set metar values
            if 'visibility' in wdata['metar']:
                visibility = c.limit(wdata['metar']['visibility'], self.conf.max_visibility)

                if not self.data.override_visibility.value:
                    self.weather.visibility.value = visibility

                self.data.visibility.value = visibility

            if 'precipitation' in wdata['metar']:
                p = wdata['metar']['precipitation']
                for precp in p:
                    precip, wet = c.metar2xpprecipitation(precp, p[precp]['int'], p[precp]['int'], p[precp]['recent'])

                    if precip is not False:
                        rain = precip
                    if wet is not False:
                        friction = wet

                if 'TS' in p:
                    ts = 0.5
                    if  p['TS']['int'] == '-':
                        ts = 0.25
                    elif p['TS']['int'] == '+':
                        ts = 1

            if not self.data.override_precipitation:
                self.weather.thunderstorm.value = ts
                self.weather.precipitation.value = rain

            self.data.metar_precipitation.value = rain
            self.data.metar_thunderstorm.value  = ts

            if not self.data.override_runway_friction:
                self.weather.runwayFriction.value = friction

            self.data.metar_runwayFriction.value = friction

            self.weather.newData = False

            # Set clouds
            if self.conf.set_clouds:
                self.weather.setClouds()

            # Update Dataref data
            self.data.updateData(wdata)

        ''' Data enforced/interpolated/transitioned on each cycle '''
        if not self.data.override_pressure.value and self.conf.set_pressure:
            # Set METAR or GFS pressure
            if 'pressure' in wdata['metar'] and wdata['metar']['pressure'] is not False:
                self.weather.setPressure(wdata['metar']['pressure'], elapsedMe)
            elif self.conf.set_pressure and 'pressure' in wdata['gfs']:
                    self.weather.setPressure(wdata['gfs']['pressure'], elapsedMe)

        # Set winds
        if not self.data.override_winds.value and self.conf.set_wind and 'winds' in wdata['gfs'] and len(wdata['gfs']['winds']):
            self.weather.setWinds(wdata['gfs']['winds'], elapsedMe)

        # Set turbulence
        if not self.data.override_turbulence.value and self.conf.set_turb:
            self.weather.setTurbulence(wdata['wafs'], elapsedMe)

        return -1

    def XPluginStop(self):
        # Destroy windows
        if self.aboutWindow:
            XPDestroyWidget(self, self.aboutWindowWidget, 1)
        if self.metarWindow:
            XPDestroyWidget(self, self.metarWindowWidget, 1)

        self.metarWindowCMD.destroy()

        XPLMUnregisterFlightLoopCallback(self, self.floop, 0)

        # kill weather server/client
        self.weather.shutdown()

        XPLMDestroyMenu(self, self.mMain)
        self.conf.pluginSave()

        # Unregister datarefs
        EasyDref.cleanup()

    def XPluginEnable(self):
        return 1

    def XPluginDisable(self):
        pass

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        if inParam == XPLM_PLUGIN_XPLANE and inMessage == XPLM_MSG_AIRPORT_LOADED:
            self.weather.startWeatherClient()
            self.newAptLoaded = True
        elif inMessage == (0x8000000 | 8090)  and inParam == 1:
            # inSimUpdater whants to shutdown
            self.XPluginStop()
