#!/usr/bin/python
'''
Example weather test client
'''
import socket
import cPickle

HOST, PORT = "127.0.0.1", 8950
data = "0.01|0.01"

lat, lon = 41.38, 2.18

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto("?%f|%f\n" % (lat, lon), (HOST, PORT))
#sock.sendto("!shutdown", (HOST, PORT))
received = sock.recv(1024*8)

print "Sent:     {}".format(data)
print cPickle.loads(received)