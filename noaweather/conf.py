import os
import cPickle
import sys
import subprocess

class Conf:
    '''
    Configuration variables
    '''
    syspath, dirsep = '', os.sep
    __VERSION__ = '2.0_beta5.2'
    
    def __init__(self, syspath):
        # Inits conf
        self.syspath      = syspath
        self.respath      = os.sep.join([self.syspath, 'Resources', 'plugins', 'PythonScripts', 'noaweather'])
        self.settingsfile = os.sep.join([self.respath, 'settings.pkl'])
        
        print self.respath
        self.cachepath    = os.sep.join([self.respath, 'cache'])
        print self.cachepath
        if not os.path.exists(self.cachepath):
            os.makedirs(self.cachepath)
        
        self.setDefautls()
        self.load()
        # Override config
        self.parserate = 1
        
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
            self.spinfo.wShowWindow = 7 # 0 or SW_SHOWMINNOACTIVE 7 
            
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
        self.enabled        = True
        self.set_wind       = True
        self.set_clouds     = True
        self.set_temp       = True
        self.set_visibility = False
        self.set_turb       = True
        self.set_pressure   = True
        self.transalt       = 32808.399000000005
        
        self.metar_agl_limit = 1300 # Below that level metar values are used. In meters.
        
        self.use_metar      = False
        self.parserate      = 1
        self.updaterate     = 1
        self.vatsim         = False
        self.download       = True
        self.max_visibility = False # in meters
        
        
        # Weather server configuration
        self.server_updaterate = 10
        self.server_address = '127.0.0.1'
        self.server_port    = 8950
        
        # Weather server variables
        self.lastgrib       = False
        self.lastwafsgrib   = False
        self.ms_update      = 0
        
        # Transitions
        self.windTransSpeed = 0.14 # kt/s
        self.windGustTransSpeed = 0.5 # kt/s
        self.windHdgTransSpeed = 1 # degrees/s
    
    def serverReloadSave(self):
        # Save server variables
        server_conf = {
                       'lastgrib': self.lastgrib,
                       'lastwafsgrib': self.lastwafsgrib,
                       'ms_update' : self.ms_update
                       }
        self.load(server_conf)
        self.save(server_conf)
           
    def save(self, append = {}):
        conf = {
                'version'   : self.__VERSION__,
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
                'download'  : self.download,
                }
        
        conf = dict(conf.items() + append.items())
        
        f = open(self.settingsfile, 'w')
        cPickle.dump(conf, f)
        f.close()
    
    def load(self, ignore={}):
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
                if var in self.__dict__ and not (var in ignore):
                    self.__dict__[var] = conf[var]
