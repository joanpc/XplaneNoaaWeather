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
from gfs import  GFS
from c import c

import SocketServer
import cPickle
import threading
import os, sys, signal
import socket
import time

from datetime import datetime

class logFile:
    '''
    File object wrapper, adds timestamp to print output
    '''
    def __init__(self, path, options):
        self.f = open(path, options)

    def write(self, data):
        if len(data) > 1:
            self.f.write('%s  %s' % ( datetime.utcnow().strftime('%b %d %H:%M:%S'), data) )
        else:
            self.f.write(data)

    def __getattr__(self, name):
        return getattr(self.f, name)

    def __setattr__(self, name, value):
        if name != 'f':
            setattr(self.f, name, value)
        else:
            self.__dict__[name] = value

class clientHandler(SocketServer.BaseRequestHandler):

    def getWeatherData(self, data):
        '''
        Prepares weather response
        '''
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

        # Parse gfs and wfas
        if gfs.lastgrib:
            response['gfs'] = gfs.parseGribData(gfs.lastgrib, lat, lon)
            response['info']['gfs_cycle'] = gfs.lastgrib
        if gfs.wafs.lastgrib:
            response['wafs'] = gfs.wafs.parseGribData(gfs.wafs.lastgrib, lat, lon)
            response['info']['wafs_cycle'] = gfs.wafs.lastgrib

        # Parse metar
        apt = gfs.metar.getClosestStation(gfs.metar.connection, lat, lon)
        if apt and len(apt) > 4:
            response['metar'] = gfs.metar.parseMetar(apt[0], apt[5], apt[3])
            response['metar']['latlon'] = (apt[1], apt[2])
            response['metar']['distance'] = c.greatCircleDistance((lat, lon), (apt[1], apt[2]))


        return response

    def shutdown(self):
        # Shutdown server. Needs to be from a different thread
        def shutNow(srv):
            srv.shutdown()

        th = threading.Thread(target = shutNow, args = (self.server, ))
        th.start()

    def handle(self):
        response = False
        data = self.request[0].strip("\n\c\t ")

        if len(data) > 1:
            if data[0] == '?':
                # weather data request
                sdata = data[1:].split('|')
                if len(sdata) > 1:
                    response = self.getWeatherData(sdata)
                elif len(data) == 5:
                    # Icao
                    response = {}
                    apt = gfs.metar.getMetar(gfs.metar.connection, data[1:])
                    if len(apt) and apt[5]:
                        response['metar'] = gfs.metar.parseMetar(apt[0], apt[5], apt[3])
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
                gfs.metar.clearMetarReports(gfs.metar.connection)
                gfs.metar.last_timestamp = 0
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
    # Get the X-Plane path from the arguments
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # Joanpc's personal debuggin options
        if sys.platform == 'win32':
            path = 'H:'
        else:
            path = '/Volumes/TO_GO/X-Plane 10'

    conf = Conf(path)

    #logfile = open(os.sep.join([conf.respath, 'weatherServerLog.txt']), 'a')

    logfile = logFile(os.sep.join([conf.respath, 'weatherServerLog.txt']), 'a')

    sys.stderr = logfile
    sys.stdout = logfile

    print '---------------'
    print 'Starting server'
    print '---------------'
    print sys.argv

    try:
        server = SocketServer.UDPServer(("localhost", conf.server_port), clientHandler)
    except socket.error:
        print "Can't bind address: %s, port: %d." % ("localhost", conf.server_port)

        if conf.weatherServerPid is not False:
            print 'Killing old server with pid %d' % conf.weatherServerPid
            os.kill(conf.weatherServerPid, signal.SIGTERM)
            time.sleep(2)
            conf.serverLoad()
            server = SocketServer.UDPServer(("localhost", conf.server_port), clientHandler)

    # Save pid
    conf.weatherServerPid = os.getpid()
    conf.serverSave()

    gfs = GFS(conf)
    gfs.start()

    print 'Server started.'

    # Server loop
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    # Close gfs worker and save config
    gfs.die.set()
    conf.serverSave()
    print 'Server stopped.'
    logfile.close()
