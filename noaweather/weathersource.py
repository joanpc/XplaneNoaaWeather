import threading
import Queue
import ssl
import urllib2
import zlib
import os
import subprocess
import sys
from util import util


class WeatherSource(object):
    """Weather source metaclass"""

    def __init__(self, conf):
        self.downloading = False
        self.download = False
        self.conf = conf

    def shutdown(self):
        """Stop pending processes"""
        if self.downloading and self.download:
            self.download.die()

    def run(self, elapsed):
        """Called by a worker thread"""
        return


class Worker(threading.Thread):
    """Runs worker functions on weather sources to periodically trigger data updating"""

    def __init__(self, workers, rate):
        self.workers = workers
        self.die = threading.Event()
        self.rate = rate
        threading.Thread.__init__(self)

    def run(self):
        while not self.die.wait(self.rate):
            for worker in self.workers:
                worker.run(self.rate)

        if self.die.isSet():
            for worker in self.workers:
                worker.shutdown()

    def shutdown(self):
        self.die.set()


class AsyncDownload:
    """Asynchronous HTTP/HTTPS downloading and decompressing"""
    def __init__(self, conf, url, cachefile, callback=False, min_size=500):

        self.callback = callback
        self.q = Queue.Queue()
        self.dirsep = conf.dirsep[:]
        cachepath = conf.cachepath[:]
        self.wgrib2bin = conf.wgrib2bin[:]
        self.cancel = threading.Event()
        self.min_size = min_size

        self.t = threading.Thread(target=self.run, args=(conf, url, cachepath, cachefile))
        self.t.start()

    def run(self, conf, url, cachepath, cachefile):
        filepath = os.sep.join([cachepath, cachefile])
        tempfile = filepath + '.tmp'

        if os.path.exists(tempfile):
            util.remove(tempfile)
        if os.path.exists(filepath):
            util.remove(filepath)

        print "Downloading: %s" % (cachefile)

        # Request gzipped file
        request = urllib2.Request(url)
        request.add_header('Accept-encoding', 'gzip, deflate')
        request.add_header('User-Agent', 'XPNOAAWeather/%s' % (conf.__VERSION__))

        if hasattr(ssl, '_create_unverified_context'):
            params = {'context': ssl._create_unverified_context()}
        else:
            params = {}

        try:
            response = urllib2.urlopen(request, **params)
        except urllib2.URLError:
            print "Download error: %s %s" % (sys.exc_info()[0], sys.exc_info()[1])
            self.q.put(False)

        # Check for gzip content
        is_gzip = url[-3:] == '.gz' or response.headers.get('content-encoding', '').find('gzip') > -1

        gz = zlib.decompressobj(16 + zlib.MAX_WBITS)

        binary = ''
        if filepath.split('.')[-1] == 'grib2':
            binary = 'b'

        of = open(tempfile, 'w' + binary)

        try:
            while True:
                if self.cancel.isSet():
                    raise Exception()
                data = response.read(1024 * 128)
                if not data:
                    print 'Downloaded: %s' % (cachefile)
                    break
                if is_gzip:
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

                print "Decompressing grib: %s %s" % (self.wgrib2bin, tempfile)

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
            # Too small, remove file.
            if os.path.exists(tempfile):
                util.remove(tempfile)
            self.q.put(False)

    def die(self):
        if self.t.is_alive():
            self.cancel.set()
            self.t.join()