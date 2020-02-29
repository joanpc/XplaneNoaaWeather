import threading


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