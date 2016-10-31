'''
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2015 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
'''

import os
import cPickle
import sys
import subprocess

class Conf:
    '''
    Configuration variables
    '''
    syspath, dirsep = '', os.sep
    printableChars = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~ '

    __VERSION__ = '2.4.2'

    def __init__(self, syspath):
        # Inits conf
        self.syspath      = syspath
        self.respath      = os.sep.join([self.syspath, 'Resources', 'plugins', 'PythonScripts', 'noaweather'])
        self.settingsfile = os.sep.join([self.respath, 'settings.pkl'])
        self.serverSettingsFile = os.sep.join([self.respath, 'weatherServer.pkl'])

        self.cachepath    = os.sep.join([self.respath, 'cache'])
        if not os.path.exists(self.cachepath):
            os.makedirs(self.cachepath)

        self.setDefautls()
        self.pluginLoad()
        self.serverLoad()

        # Config Overrides
        self.parserate = 1
        self.metar_agl_limit = 10

        if self.lastgrib and not os.path.exists(os.sep.join([self.cachepath, self.lastgrib])):
            self.lastgrib = False

        if self.lastwafsgrib and not os.path.exists(os.sep.join([self.cachepath, self.lastwafsgrib])):
            self.lastwafsgrib = False

        # Selects the apropiate wgrib binary
        platform = sys.platform
        self.spinfo = False

        self.pythonpath = sys.executable
        self.win32 = False

        if platform == 'darwin':
            sysname, nodename, release, version, machine = os.uname()
            if float(release[0:4]) > 10.6:
                wgbin = 'OSX106wgrib2'
            else:
                wgbin = 'OSX106wgrib2'
        elif platform == 'win32':
            self.win32 = True
            # Set environ for cygwin
            os.environ['CYGWIN'] = 'nodosfilewarning'
            wgbin = 'WIN32wgrib2.exe'
            self.pythonpath = os.sep.join([sys.exec_prefix, 'python.exe'])
            # Hide wgrib window for windows users
            self.spinfo = subprocess.STARTUPINFO()

            self.spinfo.dwFlags |= 1 # STARTF_USESHOWWINDOW
            self.spinfo.wShowWindow = 0 # 0 or SW_HIDE 0

        else:
            # Linux?
            wgbin = 'linux-glib2.5-i686-wgrib2'

        self.wgrib2bin  = os.sep.join([self.respath, 'bin', wgbin])

        # Enforce execution rights
        try:
            os.chmod(self.wgrib2bin, 0775)
        except:
            pass

    def setDefautls(self):
        # Default and storable settings

        # User settings
        self.enabled        = True
        self.set_wind       = True
        self.set_clouds     = True
        self.set_temp       = True
        self.set_visibility = False
        self.set_turb       = True
        self.set_pressure   = True
        self.turbulence_probability = 1

        self.inputbug       = False

        # From this AGL level METAR values are interpolated to GFS ones.
        self.metar_agl_limit = 10 # In meters
        # From this distance from the airport gfs data is used for temp, dew, pressure and clouds
        self.metar_distance_limit = 100000 # In meters

        self.parserate      = 1
        self.updaterate     = 1
        self.download       = True
        self.keepOldFiles   = False

        # Performance tweaks
        self.max_visibility = False # in SM
        self.max_cloud_height = False # in feet

        # Weather server configuration
        self.server_updaterate = 10 # Run the weather loop each #seconds
        self.server_address = '127.0.0.1'
        self.server_port    = 8950

        # Weather server variables
        self.lastgrib       = False
        self.lastwafsgrib   = False
        self.ms_update      = 0

        self.weatherServerPid = False

        # Transitions
        self.windTransSpeed = 0.14 # kt/s
        self.windGustTransSpeed = 0.5 # kt/s
        self.windHdgTransSpeed = 0.5# degrees/s

        self.metar_source = 'NOAA'
        self.metar_updaterate = 5 # minutes

        self.tracker_uid = False
        self.tracker_enabled = True

        self.ignore_metar_stations = []

        self.updateMetarRWX = True

    def saveSettings(self, filepath, settings):
        f = open(filepath, 'w')
        cPickle.dump(settings, f)
        f.close()

    def loadSettings(self, filepath):
        if os.path.exists(filepath):
            f = open(filepath, 'r')
            try:
                conf = cPickle.load(f)
                f.close()
            except:
                # Corrupted settings, remove file
                os.remove(filepath)
                return

            # Reset settings on different versions.
            if not 'version' in conf or conf['version'] < '2.0':
                return

            # may be "dangerous" if someone messes our config file
            for var in conf:
                if var in self.__dict__:
                    self.__dict__[var] = conf[var]

            # Versions config overrides
            if 'version' in conf:
                if conf['version'] < '2.3.1':
                    # Enforce metar station update
                    self.ms_update = 0
                if conf['version'] < '2.4.0':
                    # Clean ignore stations
                    self.ignore_metar_stations = []

    def pluginSave(self):
        '''Save plugin settings'''
        conf = {
                'version'   : self.__VERSION__,
                'set_temp'  : self.set_temp,
                'set_clouds': self.set_clouds,
                'set_wind'  : self.set_wind,
                'set_turb'  : self.set_turb,
                'set_pressure' : self.set_pressure,
                'enabled'   : self.enabled,
                'updaterate': self.updaterate,
                'metar_source': self.metar_source,
                'download'  : self.download,
                'metar_agl_limit': self.metar_agl_limit,
                'metar_distance_limit': self.metar_distance_limit,
                'max_visibility': self.max_visibility,
                'max_cloud_height': self.max_cloud_height,
                'turbulence_probability': self.turbulence_probability,
                'inputbug': self.inputbug,
                'metar_updaterate': self.metar_updaterate,
                'tracker_uid': self.tracker_uid,
                'tracker_enabled': self.tracker_enabled,
                'ignore_metar_stations': self.ignore_metar_stations
                }
        self.saveSettings(self.settingsfile, conf)

    def pluginLoad(self):
        self.loadSettings(self.settingsfile)

        if self.metar_source == 'NOAA':
            self.metar_updaterate = 5
        else:
            self.metar_updaterate = 10

    def serverSave(self):
        '''Save weather server settings'''
        server_conf = {
                       'version'   : self.__VERSION__,
                       'lastgrib': self.lastgrib,
                       'lastwafsgrib': self.lastwafsgrib,
                       'ms_update' : self.ms_update,
                       'weatherServerPid': self.weatherServerPid,
                       }
        self.saveSettings(self.serverSettingsFile, server_conf)

    def serverLoad(self):
        self.pluginLoad()
        self.loadSettings(self.serverSettingsFile)
