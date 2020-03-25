#!/usb/bin/python
'''
NOAA weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2015 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
'''

from conf import Conf
from gfs import GFS
from wafs import WAFS
from metar import Metar
from weathersource import Worker
from c import c

import SocketServer
import cPickle
import os, sys, signal
import socket
import time

from datetime import datetime
import threading


class LogFile:
    """File object wrapper, adds timestamp to print output"""

    def __init__(self, path, options):
        self.f = open(path, options)

    def write(self, data):
        if len(data) > 1:
            self.f.write('%s  %s' % (datetime.utcnow().strftime('%b %d %H:%M:%S'), data))
        else:
            self.f.write(data)

    def __getattr__(self, name):
        return getattr(self.f, name)

    def __setattr__(self, name, value):
        if name != 'f':
            setattr(self.f, name, value)
        else:
            self.__dict__[name] = value

class ClientHandler(SocketServer.BaseRequestHandler):

    @staticmethod
    def get_weather_data(data):
        """Collects weather data for the response"""

        lat, lon = float(data[0]), float(data[1])

        response = {
            'gfs': {},
            'wafs': {},
            'metar': {},
            'info': {'lat': lat,
                     'lon': lon,
                     'wafs_cycle': 'na',
                     'gfs_cycle': 'na'
                     }
        }

        lat, lon = float(data[0]), float(data[1])

        if lat > 98 and lon > 98:
            return False

        # Parse gfs and wafs
        if gfs.last_grib:
            grib_path = os.path.sep.join([gfs.cache_path, gfs.last_grib])
            response['gfs'] = gfs.parse_grib_data(grib_path, lat, lon)
            response['info']['gfs_cycle'] = gfs.last_grib
        if wafs.last_grib:
            grib_path = os.path.sep.join([wafs.cache_path, wafs.last_grib])
            response['wafs'] = wafs.parse_grib_data(grib_path, lat, lon)
            response['info']['wafs_cycle'] = wafs.last_grib

        # Parse metar
        apt = metar.get_closest_station(metar.connection, lat, lon)
        if apt and len(apt) > 4:
            response['metar'] = metar.parse_metar(apt[0], apt[5], apt[3])
            response['metar']['latlon'] = (apt[1], apt[2])
            response['metar']['distance'] = c.greatCircleDistance((lat, lon), (apt[1], apt[2]))

        return response

    def shutdown(self):
        # shutdown Needs to be from called from a different thread
        def shut_down_now(srv):
            srv.shutdown()

        th = threading.Thread(target=shut_down_now, args=(self.server,))
        th.start()

    def handle(self):
        response = False
        data = self.request[0].strip("\n\c\t ")

        if len(data) > 1:
            if data[0] == '?':
                # weather data request
                sdata = data[1:].split('|')
                if len(sdata) > 1:
                    response = self.get_weather_data(sdata)
                elif len(data) == 5:
                    # Icao
                    response = {}
                    apt = metar.get_metar(metar.connection, data[1:])
                    if len(apt) and apt[5]:
                        response['metar'] = metar.parse_metar(apt[0], apt[5], apt[3])
                    else:
                        response['metar'] = {'icao': 'METAR STATION',
                                             'metar': 'NOT AVAILABLE'}

            elif data == '!shutdown':
                conf.serverSave()
                self.shutdown()
                response = '!bye'
            elif data == '!reload':
                conf.serverSave()
                conf.pluginLoad()
            elif data == '!resetMetar':
                # Clear database and force redownload
                metar.clear_reports(metar.connection)
                metar.last_timestamp = 0
            elif data == '!ping':
                response = '!pong'
            else:
                return

        socket = self.request[1]
        nbytes = 0

        if response:
            response = cPickle.dumps(response)
            socket.sendto(response + "\n", self.client_address)
            nbytes = sys.getsizeof(response)

        print '%s:%s: %d bytes sent.' % (self.client_address[0], data, nbytes)


if __name__ == "__main__":
    debug = False
    # Get the X-Plane path from the arguments
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # Debug run
        debug = True
        path = False

    conf = Conf(path)

    if not debug:
        logfile = LogFile(os.sep.join([conf.respath, 'weatherServerLog.txt']), 'a')

        sys.stderr = logfile
        sys.stdout = logfile

    print '---------------'
    print 'Starting server'
    print '---------------'
    print sys.argv

    try:
        server = SocketServer.UDPServer(("localhost", conf.server_port), ClientHandler)
    except socket.error:
        print "Can't bind address: %s, port: %d." % ("localhost", conf.server_port)

        if conf.weatherServerPid is not False:
            print 'Killing old server with pid %d' % conf.weatherServerPid
            os.kill(conf.weatherServerPid, signal.SIGTERM)
            time.sleep(2)
            conf.serverLoad()
            server = SocketServer.UDPServer(("localhost", conf.server_port), ClientHandler)

    # Save pid
    conf.weatherServerPid = os.getpid()
    conf.serverSave()

    # Weather classes
    gfs = GFS(conf)
    metar = Metar(conf)
    wafs = WAFS(conf)

    # Init worker thread
    worker = Worker([gfs, metar, wafs], conf.parserate)
    worker.start()

    print 'Server started.'

    # Server loop
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    # Close gfs worker and save config
    worker.shutdown()
    conf.serverSave()
    sys.stdout.flush()

    print 'Server stopped.'

    if not debug:
        logfile.close()
