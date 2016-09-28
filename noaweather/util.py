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
import sys

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
                    try:
                        os.rename(filepath, npath)
                    except:
                        print "can't rename %s" % (filepath)
                        if sys.platform == 'win32':
                            import ctypes
                            print '%s marked for deletion on reboot.' % (filepath)
                            ctypes.windll.kernel32.MoveFileExA(filepath, None, 4)
                    break
                i += 1

    @classmethod
    def rename(cls, opath, dpath):
        if os.path.exists(dpath):
            cls.remove(dpath)
        try:
            os.rename(opath, dpath)
        except:
            print "Can't rename: %s to %s, trying to copy/remove" % (opath, dpath)
            cls.copy(opath, dpath)
            cls.remove(opath)

    @classmethod
    def copy(cls, opath, dpath):
        if os.path.exists(dpath):
            cls.remove(dpath)
        try:
            shutil.copyfile(opath, dpath)
        except:
            print "Can't copy %s to %s" % (opath, dpath)
