#!/usr/bin/python
'''
Example weather test client
'''
import socket
import cPickle

# tests requests
tests = [
         "?%f|%f" % (41.38, 2.18), # Request weather data for lat/lon
         '?LEBL', # Request metar of the station
         '?KSEA',
         '?SKBO',
         #'!reload',     # Reload configuration
         #'!shutdown',   # Shutdown server
         ]

HOST, PORT = "127.0.0.1", 8950

for request in tests:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(request, (HOST, PORT))
    received = sock.recv(1024*8)

    print "Request: %s \nResponse: %s" % (request, cPickle.loads(received))
