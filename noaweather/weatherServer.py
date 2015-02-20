#!/usb/bin/python
'''
Noa weather daemon server
'''

from conf import Conf
from gfs import  GFS

import SocketServer
import cPickle
import threading

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
        response['gfs'] = gfs.parseGribData(gfs.conf.lastgrib, lat, lon)
        response['wafs'] = gfs.wafs.parseGribData(gfs.conf.lastwafsgrib, lat, lon)
            
        # Parse metar
        apt = gfs.metar.getClosestStation(gfs.metar.connection, lat, lon)
        response['metar'] = gfs.metar.parseMetar(apt[0], apt[5], apt[3])
        
        return response
    
    def shutdownshutdown(self):
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

    server = SocketServer.UDPServer(("localhost", conf.server_port), clientHandler)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    
    gfs.die.set()
    conf.save()
    
    
    
    