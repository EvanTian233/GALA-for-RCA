# encoding = utf-8

import logging


class Logger:
    def __init__(self, logname, loglevel=logging.DEBUG, loggername=None):
        """Create a logger that writes to both a file and stdout."""
        self.logger = logging.getLogger(loggername)
        self.logger.setLevel(loglevel)

        file_handler = logging.FileHandler(logname, encoding="utf-8")
        file_handler.setLevel(loglevel)

        if not self.logger.handlers:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(loglevel)
            formatter = logging.Formatter(
                "[%(levelname)s]%(asctime)s %(filename)s:%(lineno)d: %(message)s"
            )
            file_handler.setFormatter(formatter)
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.addHandler(stream_handler)

    def getlog(self):
        return self.logger
