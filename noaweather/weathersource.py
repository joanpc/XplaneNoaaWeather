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
