"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2020 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import threading
import ssl
try:
    from urllib2 import Request, urlopen, URLError
except ImportError:
    from urllib.request import Request, urlopen, URLError
import zlib
import os
import subprocess
import sys
import io
from datetime import datetime, timedelta
from tempfile import TemporaryFile

try:
    from util import util
    from conf import Conf
except ImportError:
    from .util import util
    from .conf import Conf


class WeatherSource(object):
    """Weather source metaclass"""

    cache_path = False

    def __init__(self, conf):
        self.download = False
        self.conf = conf
        self.die = threading.Event()

        if not self.cache_path:
            self.cache_path = self.conf.cachepath

        if self.cache_path and not os.path.exists(self.cache_path):
            os.makedirs(self.cache_path)

    def shutdown(self):
        """Stop pending processes"""
        self.die.set()

    def run(self, elapsed):
        """Called by a worker thread"""
        return


class GribWeatherSource(WeatherSource):
    """Grib file weather source"""

    cycles = range(0, 24, 6)
    publish_delay = {'hours': 4, 'minutes': 25}
    variable_list = []
    download_wait = 0
    grib_conf_var = 'lastgrib'

    def __init__(self, conf):
        self.cache_path = os.path.sep.join([conf.cachepath, 'gfs'])

        super(GribWeatherSource, self).__init__(conf)

        if self.last_grib and not os.path.isfile(os.path.sep.join([self.cache_path, self.last_grib])):
            self.last_grib = False

    @classmethod
    def get_cycle_date(cls):
        """Returns last cycle date available"""

        now = datetime.utcnow()
        # cycle is published with 4 hours 25min delay
        cnow = now - timedelta(**cls.publish_delay)
        # get last cycle
        for cycle in cls.cycles:
            if cnow.hour >= cycle:
                lcycle = cycle
        # Forecast
        adjs = 0
        if cnow.day != now.day:
            adjs = +24
        forecast = (adjs + now.hour - lcycle) / 3 * 3

        return '%d%02d%02d' % (cnow.year, cnow.month, cnow.day), lcycle, forecast

    def run(self, elapsed):
        """Worker function called by a worker thread to update the data"""

        if not self.conf.download:
            return

        if self.download_wait:
            self.download_wait -= elapsed
            return

        datecycle, cycle, forecast = self.get_cycle_date()
        # forecast wrong number
        remnd = forecast % 3
        if remnd > 0:
            forecast -= remnd
        # forecast wrong number
        cache_file = self.get_cache_filename(datecycle, cycle, forecast)

        if not self.download:
            cache_file_path = os.sep.join([self.cache_path, cache_file])

            if self.last_grib == cache_file and os.path.isfile(cache_file_path):
                # Nothing to do
                return
            else:
                # Trigger new download
                url = self.get_download_url(datecycle, cycle, forecast)
                print('Downloading: %s' % cache_file)
                self.download = AsyncTask(GribDownloader.download,
                                          url,
                                          cache_file_path,
                                          binary=True,
                                          variable_list=self.variable_list,
                                          cancel_event=self.die,
                                          decompress=self.conf.wgrib2bin,
                                          spinfo=self.conf.spinfo)
                self.download.start()
        else:
            if not self.download.pending():

                self.download.join()
                if isinstance(self.download.result, Exception):
                    print('Error Downloading Grib file: %s.' % str(self.download.result))
                    if os.path.isfile(cache_file):
                        util.remove(os.sep.join([self.cache_path, cache_file]))
                    # wait a try again
                    self.download_wait = 60
                else:
                    # New file available
                    if not self.conf.keepOldFiles and self.last_grib:
                        util.remove(os.path.sep.join([self.cache_path, self.last_grib]))
                    self.last_grib = str(self.download.result.split(os.path.sep)[-1])
                    print('%s successfully downloaded.' % self.last_grib)

                # reset download
                self.download = False
            else:
                # Waiting for download
                return

    def __getattr__(self, item):
        if item == 'last_grib':
            return getattr(self.conf, self.grib_conf_var)
        return self.__getattribute__(item)

    def __setattr__(self, key, value):
        if key == 'last_grib':
            self.conf.__dict__[self.grib_conf_var] = value
        self.__dict__[key] = value


class Worker(threading.Thread):
    """Creates a new thread to periodically run worker functions on weather sources to trigger
    data updating or other tasks

    Attributes:
        workers (list): Worker functions to be called
        die (threading.Event): Se the flag to end the thread
        rate (int): wait rate seconds between runs
    """

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
        if self.is_alive():
            self.die.set()
            self.join(3)


class AsyncTask(threading.Thread):
    """Run an asynchronous task on a new thread

    Attributes:
        task (method): Worker method to be called
        die (threading.Event): Set the flag to end the tasks
        result (): return of the task method
    """

    def __init__(self, task, *args, **kwargs):

        self.task = task
        self.cancel = threading.Event()
        self.kwargs = kwargs
        self.args = args
        self.result = False
        threading.Thread.__init__(self)

        self.pending = self.is_alive

    def run(self):
        try:
            self.result = self.task(*self.args, **self.kwargs)
        except Exception as result:
            self.result = result
        return

    def stop(self):
        if self.is_alive():
            self.cancel.set()
            self.join(3)


class GribDownloader(object):
    """Grib download utilities"""

    @staticmethod
    def decompress_grib(path_in, path_out, wgrib2bin, spinfo=False):
        """Unpacks grib file using wgrib2 binary
        """

        args = [wgrib2bin, path_in, '-set_grib_type', 'simple', '-grib_out', path_out]
        kwargs = {'stdout': sys.stdout, 'stderr': sys.stderr}

        if spinfo:
            kwargs.update({'shell': True, 'startupinfo': spinfo})

        p = subprocess.Popen(args, **kwargs)
        p.wait()

    @staticmethod
    def download_part(url, file_out, start=0, end=0, **kwargs):
        """File Downloader supports gzip and cancel

        Args:
            url (str): the url to download
            file_out (file): Output file descriptor

            start (int): start bytes for partial download
            end (int): end bytes for partial download

        Kwargs:
            cancel_event (threading.Event): Cancel download setting the flag
            user_agent (str): User-Agent HTTP header

        """

        req = Request(url)
        req.add_header('Accept-encoding', 'gzip, deflate')

        user_agent = kwargs.pop('user_agent', 'XPNOAAWeather/%s' % Conf.__VERSION__)
        req.add_header('User-Agent', user_agent)

        # Partial download headers
        if start or end:
            req.headers['Range'] = 'bytes=%d-%d' % (start, end)

        if hasattr(ssl, '_create_unverified_context'):
            params = {'context': ssl._create_unverified_context()}
        else:
            params = {}

        print("Downloading part of {} with params: {}".format(url, params))
        response = urlopen(req, **params)

        gz = False
        if url[-3:] == '.gz' or response.headers.get('content-encoding', '').find('gzip') > -1:
            print("needs to be decompressed")
            gz = zlib.decompressobj(16 + zlib.MAX_WBITS)

        cancel = kwargs.pop('cancel_event', False)

        while True:
            if cancel and cancel.isSet():
                raise GribDownloaderCancel("Download canceled by user.")

            data = response.read(1024 * 128)
            print("got more data")
            if not data:
                # End of file
                break
            if gz:
                data = gz.decompress(data)
                print("has been decompressed")
            if isinstance(data, bytes):
                if isinstance(file_out, io.TextIOWrapper):
                    data = data.decode(file_out.encoding)
            file_out.write(data)

    @staticmethod
    def to_download(level, var, variable_list):
        """Returns true if level/var combination is in the download list"""
        for group in variable_list:
            if var in group['vars'] and level in group['levels']:
                return True
        return False

    @classmethod
    def gen_chunk_list(cls, grib_index, variable_list):
        """Returns a download list from a grib index and a variable list

        Args:
            grib_index (list): parsed grib index
            variable_list (list): list of dicts defining data to download
                                  [{'levels': [], 'vars': []}, ]

        Returns:
            list: The chunk list [[start, stop], ]

        """
        chunk_list = []
        end = False

        for line in reversed(grib_index):
            start, var, level = line[1], line[3], line[4]
            if cls.to_download(level, var, variable_list):
                if end:
                    end -= 1
                chunk_list.append([start, end])
            end = start

        chunk_list.reverse()

        return chunk_list

    @staticmethod
    def parse_grib_index(index_file):
        """Returns

        args:
            index_file (file): grib idx file

        Return:
            list: The table index

        Index sample:
            1:0:d=2020022418:HGT:100 mb:6 hour fcst:
            2:38409:d=2020022418:TMP:100 mb:6 hour fcst:

        """

        index = []
        for line in iter(index_file):
            cols = line.split(':')
            if len(cols) != 7:
                raise RuntimeError("Bad GRIB file index format: Missing columns")
            try:
                cols[1] = int(cols[1])
            except ValueError:
                raise RuntimeError("Bad GRIB file index format: Bad integer")

            index.append(cols)

        return index

    @classmethod
    def download(cls, url, file_path, binary=False, **kwargs):
        """Download grib for the specified variable_lists

            Args:
                url (str): URL to the grib file excluding the extension
                file_path (str): Path to the output file
                binary (bool): Set to True for binary files or files will get corrupted on Windows.

            Kwargs:
                cancel_event (threading.Event): Set the flat to cancel the download at any time
                variable_list (list): List of variables dicts ex: [{'level': ['500mb', ], 'vars': 'TMP'}, ]
                decompress (str): Path to the wgrib2 to decompress the file.

            Returns:
                str: the path to the final file on success

            Raises:
                GribDownloaderError: on fail.
                GribDownloaderCancel: on cancel.
        """

        if sys.version_info.major == 3:
            binary = True
        variable_list = kwargs.pop('variable_list', [])

        if variable_list:
            # Download the index and create a chunk list
            with TemporaryFile('a+') as idx_file:
                idx_file.seek(0)
                try:
                    cls.download_part('%s.idx' % url, idx_file, **kwargs)
                except URLError:
                    raise GribDownloaderError('Unable to download index file for: %s' % url)

                idx_file.seek(0)

                index = cls.parse_grib_index(idx_file)
                chunk_list = cls.gen_chunk_list(index, variable_list)

        flags = 'wb' if binary else 'w'

        with open(file_path, flags) as grib_file:
            if not variable_list:
                # Fake chunk list for non filtered files
                chunk_list = [[False, False]]

            for chunk in chunk_list:
                try:
                    cls.download_part('%s' % url, grib_file, start=chunk[0], end=chunk[1], **kwargs)
                except URLError as err:
                    raise GribDownloaderError('Unable to open url: %s\n\t%s' % (url, str(err)))

        wgrib2 = kwargs.pop('decompress', False)
        spinfo = kwargs.pop('spinfo', False)
        if wgrib2:
            tmp_file = "%s.tmp" % file_path
            try:
                os.rename(file_path, tmp_file)
                cls.decompress_grib(tmp_file, file_path, wgrib2, spinfo)
                util.remove(tmp_file)
            except OSError as err:
                raise GribDownloaderError('Unable to decompress: %s \n\t%s' % (file_path, str(err)))

        return file_path


class GribDownloaderError(Exception):
    """Raised on a download error"""


class GribDownloaderCancel(Exception):
    """Raised when a download is canceled by user intervention"""
