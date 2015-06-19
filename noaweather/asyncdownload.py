'''
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2015 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
'''

import threading
import Queue
import urllib2
import zlib
import os
import subprocess
import sys
from util import util

class AsyncDownload():
    '''
    Asyncronous download
    '''
    def __init__(self, conf, url, cachefile, callback = False, min_size = 500):
        
        self.callback = callback
        self.q = Queue.Queue()
        self.dirsep = conf.dirsep[:]
        cachepath = conf.cachepath[:]
        self.wgrib2bin = conf.wgrib2bin[:]
        self.cancel = threading.Event()
        self.min_size = min_size
        
        self.t = threading.Thread(target = self.run, args = (conf, url, cachepath, cachefile))
        self.t.start()
        
    def run(self, conf, url, cachepath, cachefile):
        filepath = os.sep.join([cachepath, cachefile])
        tempfile = filepath + '.tmp'
        
        if os.path.exists(tempfile):
            util.remove(tempfile)
        if os.path.exists(filepath):
            util.remove(filepath)
        
        print "Dowloading: %s" % (cachefile)
        
        # Request gzipped file
        request = urllib2.Request(url)
        request.add_header('Accept-encoding', 'gzip,deflate')
        
        try:
            response = urllib2.urlopen(request)
        except urllib2.URLError:
            return
        
        # Check for gzziped file
        isGzip = response.headers.get('content-encoding', '').find('gzip') >= 0
        gz = zlib.decompressobj(16+zlib.MAX_WBITS)
        
        binary = ''
        if filepath.split('.')[-1] == 'grib2':
            binary = 'b'
        
        of = open(tempfile, 'w' + binary)
        
        try:
            while True:
                if self.cancel.isSet():
                    raise Exception()
                data = response.read(1024*128)
                if not data:
                    print 'Downloaded: %s' % (cachefile)
                    break
                if isGzip:
                    data = gz.decompress(data)
                of.write(data)
        except Exception:
            if os.path.exists(tempfile):
                util.remove(tempfile)
            self.q.put(False)
        
        of.close()
        
        if os.path.exists(tempfile) and os.path.getsize(tempfile) > self.min_size:
            # Downloaded
            if filepath.split('.')[-1] == 'grib2':
                # Uncompress grib2 file
                print "Uncompressing grib: %s %s" % (self.wgrib2bin, tempfile)
                
                args = [self.wgrib2bin, tempfile, '-set_grib_type', 'simple', '-grib_out', filepath]         
        
                if conf.spinfo:
                    p = subprocess.Popen(args, startupinfo=conf.spinfo, stdout=sys.stdout, stderr=sys.stderr, shell=True)
                else:
                    p = subprocess.Popen(args, stdout=sys.stdout, stderr=sys.stderr)
                p.wait()
                
                util.remove(tempfile)
                
            else:
                util.rename(tempfile, filepath)  
           
            # Call callback if defined otherwise put the file on the queue
            if self.callback:
                self.callback(cachefile)
            else:
                self.q.put(cachefile)
        else:
            # File to small, remove file.
            if os.path.exists(tempfile):
                util.remove(tempfile)
            self.q.put(False)
        
    def die(self):
        if self.t.is_alive():
            self.cancel.set()
            self.t.join()
