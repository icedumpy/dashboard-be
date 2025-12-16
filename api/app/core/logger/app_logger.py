# app/logging_filters.py
import logging


class ExcludeHlsAccessFilter(logging.Filter):
    """
    Drop uvicorn access logs for /hls/* requests, keep everything else.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Message looks like: '172.20.0.1:37346 - "GET /hls/channel_4.m3u8 HTTP/1.1" 304 Not Modified'
        return '"/hls/' not in msg
