'''
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2015 Joan Perez i Cauhe

Basic tracking using piwik

---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
'''
import thread
import ssl
import urllib, urllib2
import platform
import json
import struct
import string
import random

from XPLMUtilities import *

class Tracker:

    TRACKER_URL = 'https://analytics.joanpc.com/piwik.php'

    def __init__(self, conf, site_id, base_path = ''):

        self.conf = conf
        self.base_path = base_path

        if not self.conf.tracker_uid:
            import uuid
            self.conf.tracker_uid  = str(uuid.uuid5(uuid.uuid1(), 'xjpc').fields[-1])

        self.site_id = site_id

        xpver, sdkver, hid = XPLMGetVersions()
        uname = platform.uname()

        self.cvars = json.dumps({
            "1": ['xp_ver',  xpver],
            "2": ['plugin_ver', self.conf.__VERSION__],
            "3": ['os', uname[0]],
            "4": ['os_ver', uname[2]],
            "5": ['platform', struct.calcsize("P") * 8],
        })

        self.userAgent = 'X-Plane/%s (%s ; %s/%s ; %s)' % (xpver, self.conf.__VERSION__, uname[0], uname[2], platform.platform())

    def track(self, url, action_name = '', params = {}):
        if self.conf.tracker_enabled:
            thread.start_new_thread(self._track, (url, action_name, params))

    def _track(self, url, action_name = '', params = {}):
        tparams = {'idsite': self.site_id,
                                    'rec': 1,
                                    'apiv': 1,
                                    'url': '/'.join([self.base_path, url]),
                                    'action_name': action_name,
                                    'rand': ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4)),
                                    'cookie': 0,
                                    'uid': self.conf.tracker_uid,
                                    '_cvar': self.cvars,
                                    'send_image': 0,
                                    }
        tparams.update(params)

        req = urllib2.Request(self.TRACKER_URL, urllib.urlencode(tparams), {'User-Agent': self.userAgent})

        if hasattr(ssl, '_create_unverified_context'):
            ctx = {'context': ssl._create_unverified_context()}
        else:
            ctx = {}

        try:
            urllib2.urlopen(req, **ctx)
        except Exception:
            pass
