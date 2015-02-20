import os
import cPickle
import sys
import subprocess

__VERSION__ = '2.0_beta1.1'

class Conf:
    '''
    Configuration variables
    '''
    syspath, dirsep = '','/'
    
    def __init__(self, syspath):
        # Inits conf
        self.syspath      = syspath
        self.respath      = self.dirsep.join([self.syspath, 'Resources', 'plugins', 'PythonScripts', 'noaweather'])
        self.settingsfile = self.respath + self.dirsep + 'settings.pkl'
        
        self.cachepath    = self.dirsep.join([self.respath, 'cache'])
        if not os.path.exists(self.cachepath):
            os.makedirs(self.cachepath)
        
        self.setDefautls()
        self.load()
        # Override config
        self.parserate = 1
        
        if self.lastgrib and not os.path.exists(self.cachepath + self.dirsep + self.lastgrib):
            self.lastgrib = False
            
        if self.lastwafsgrib and not os.path.exists(self.cachepath + self.dirsep + self.lastwafsgrib):
            self.lastwafsgrib = False
        
        # Selects the apropiate wgrib binary
        platform = sys.platform
        self.spinfo = False
        
        self.pythonpath = sys.executable
        
        if platform == 'darwin':
            sysname, nodename, release, version, machine = os.uname()
            if float(release[0:4]) > 10.6:
                wgbin = 'OSX106wgrib2'
            else:
                wgbin = 'OSX106wgrib2'
        elif platform == 'win32':
            wgbin = 'WIN32wgrib2.exe'
            self.pythonpath = sys.exec_prefix + '/pythonw.exe'
            # Hide wgrib window for windows users
            self.spinfo = subprocess.STARTUPINFO()
            self.spinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self.spinfo.wShowWindow = subprocess.SW_HIDE # 0 or SW_SHOWMINNOACTIVE 7 
            
        else:
            # Linux?
            wgbin = 'linux-glib2.5-i686-wgrib2'
        
        self.wgrib2bin  = self.dirsep.join([self.respath, 'bin', wgbin])
        
        # Enforce execution rights
        try:
            os.chmod(self.wgrib2bin, 0775)
        except:
            pass

    def setDefautls(self):
        # Default and storable settings
        self.enabled        = True
        self.set_wind       = True
        self.set_clouds     = True
        self.set_temp       = True
        self.set_visibility = False
        self.set_turb       = True
        self.set_pressure   = True
        self.transalt       = 32808.399000000005
        self.use_metar      = False
        self.lastgrib       = False
        self.lastwafsgrib   = False
        self.updaterate     = 4
        self.parserate      = 0.1
        self.updaterate     = 1
        self.server_updaterate = 10
        self.vatsim         = False
        self.download       = True
        self.ms_update      = 0
        self.max_visibility = 10000 # in meters
        self.server_port    = 8950

    def save(self):
        conf = {
                'version'   : __VERSION__,
                'lastgrib'  : self.lastgrib,
                'set_temp'  : self.set_temp,
                'set_clouds': self.set_clouds,
                'set_wind'  : self.set_wind,
                'set_turb'  : self.set_turb,
                'set_pressure' : self.set_pressure,
                'transalt'  : self.transalt,
                'use_metar' : self.use_metar,
                'enabled'   : self.enabled,
                'updaterate': self.updaterate,
                'vatsim'    : self.vatsim,
                'lastwafsgrib' : self.lastwafsgrib,
                'download'  : self.download,
                'ms_update' : self.ms_update
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
            
            # may be "dangerous" if someone messes our config file
            for var in conf:
                if var in self.__dict__:
                    self.__dict__[var] = conf[var]
