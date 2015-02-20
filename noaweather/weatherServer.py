#!/usb/bin/python
'''
Noa weather daemon server
'''

from conf import Conf
from gfs import  GFS

import SocketServer
import cPickle
import threading
import os, sys

class clientHandler(SocketServer.BaseRequestHandler):

    def getWeatherData(self, data):
        '''
        Prepares weather response
        '''
        response = {
            'gfs': False,
            'wafs': False,
            'metar': False
            }
        
        lat, lon = float(data[0]), float(data[1])
        
        # Parse gfs and wfas
        if gfs.conf.lastgrib and os.path.exists(gfs.conf.lastgrib):
            response['gfs'] = gfs.parseGribData(gfs.conf.lastgrib, lat, lon)
        if gfs.conf.lastwafsgrib and os.path.exists(gfs.conf.lastwafsgrib):
            response['wafs'] = gfs.wafs.parseGribData(gfs.conf.lastwafsgrib, lat, lon)
            
        # Parse metar
        apt = gfs.metar.getClosestStation(gfs.metar.connection, lat, lon)
        response['metar'] = gfs.metar.parseMetar(apt[0], apt[5], apt[3])
        
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
                data = data[1:].split('|')
                if len(data) > 1:
                    response = self.getWeatherData(data)
            elif data == '!shutdown':
                print '%s: !shutdown' % (self.client_address[0])
                self.shutdown()
            elif data == '!reload':
                # TODO: reload config
                #self.conf.reload()
                pass
                    
            else:
                return
        
        socket = self.request[1]
        
        if response:
            socket.sendto(cPickle.dumps(response), self.client_address)       
            
        print '%s : %s' % (self.client_address[0], data)

if __name__ == "__main__":
    conf = Conf('/Volumes/TO_GO/X-Plane 10')
    gfs = GFS(conf)
    gfs.start()
    
    # Open logfile
    logfile = open(conf.respath + '/weatherServerLog.txt', 'a')
    sys.stderr = logfile
    sys.stdout = logfile

    server = SocketServer.UDPServer(("localhost", conf.server_port), clientHandler)
    
    # Server loop
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    
    # Close gfs worker and save config
    gfs.die.set()
    conf.save()
    logfile.close()
    
    
    
    