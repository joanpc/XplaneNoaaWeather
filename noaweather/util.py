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
import shutil

class util:
    
    @classmethod
    def remove(cls, filepath):
        '''Try to remove a file. if fails trys to rename-it
        '''
        try:
            os.remove(filepath)
        except:
            print "can't remove %s" % (filepath)
            i = 1
            while 1:
                npath = '%s-%d' % (filepath, i)
                if not os.path.exists(npath):
                    os.rename(filepath, npath)
                    break
                i += 1
    
    @classmethod
    def rename(cls, opath, dpath):
        if os.path.exists(dpath):
            cls.remove(dpath)
        os.rename(opath, dpath)
    
    @classmethod
    def copy(cls, opath, dpath):
        if os.path.exists(dpath):
            cls.remove(dpath)
        shutil.copyfile(opath, dpath)