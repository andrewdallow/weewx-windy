# Copyright 2019-2026 Matthew Wall
# Updated 2026 for Windy v2 API

"""
This is a weewx extension that uploads data to windy.com

http://windy.com

The v2 API is described at:
https://api.windy.com/stations

The v2 API uses GET requests with query parameters and authenticates via a
PASSWORD query parameter (station password) rather than an API key in the URL.

softwaretype is automatically set to "weewx-<version>" and stationtype is
automatically derived from the driver's hardware_name property (e.g.
"Vantage Pro2") — no manual configuration needed for either.

Minimal configuration:

[StdRESTful]
    [[Windy]]
        password = STATION_PASSWORD
        station = pws-station-001
"""

# deal with differences between python 2 and python 3
try:
    # Python 3
    import queue
except ImportError:
    # Python 2
    # noinspection PyUnresolvedReferences
    import Queue as queue

try:
    # Python 3
    from urllib.parse import urlencode
    from urllib.request import urlopen, Request
except ImportError:
    # Python 2
    # noinspection PyUnresolvedReferences
    from urllib import urlencode
    # noinspection PyUnresolvedReferences
    from urllib2 import urlopen, Request

import logging
import sys
import time

import weewx
import weewx.manager
import weewx.restx
import weewx.units
from weeutil.weeutil import to_bool, to_int, version_compare

VERSION = "0.9"

REQUIRED_WEEWX = "4.1.0"
if version_compare(weewx.__version__, REQUIRED_WEEWX) < 0:
    raise weewx.UnsupportedFeature("weewx %s or greater is required, found %s"
                                   % (REQUIRED_WEEWX, weewx.__version__))

log = logging.getLogger(__name__)

def logdbg(msg):
    log.debug(msg)

def loginf(msg):
    log.info(msg)

def logerr(msg):
    log.error(msg)


class Windy(weewx.restx.StdRESTbase):
    # New v2 API endpoint
    DEFAULT_URL = 'https://api.windy.com/api/v2/observation/update'

    def __init__(self, engine, cfg_dict):
        super(Windy, self).__init__(engine, cfg_dict)
        loginf("version is %s" % VERSION)
        site_dict = weewx.restx.get_site_dict(cfg_dict, 'Windy', 'password', 'station')
        if site_dict is None:
            return

        try:
            site_dict['manager_dict'] = weewx.manager.get_manager_dict_from_config(
                cfg_dict, 'wx_binding')
        except weewx.UnknownBinding:
            pass

        # Derive metadata directly from weewx rather than requiring config
        site_dict.setdefault('softwaretype', 'weewx-%s' % weewx.__version__)
        site_dict.setdefault('stationtype', engine.stn_info.hardware)

        self.archive_queue = queue.Queue()
        self.archive_thread = WindyThread(self.archive_queue, **site_dict)

        self.archive_thread.start()
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)


class WindyThread(weewx.restx.RESTThread):

    def __init__(self, q, password, station, server_url=Windy.DEFAULT_URL,
                 softwaretype=None, stationtype=None,
                 skip_upload=False, manager_dict=None,
                 post_interval=None, max_backlog=sys.maxsize, stale=None,
                 log_success=True, log_failure=True,
                 timeout=60, max_tries=3, retry_wait=5):
        super(WindyThread, self).__init__(q,
                                         protocol_name='Windy',
                                         manager_dict=manager_dict,
                                         post_interval=post_interval,
                                         max_backlog=max_backlog,
                                         stale=stale,
                                         log_success=log_success,
                                         log_failure=log_failure,
                                         max_tries=max_tries,
                                         timeout=timeout,
                                         retry_wait=retry_wait)
        self.password = password
        self.station = station          # string station ID, e.g. "pws-station-001"
        self.server_url = server_url
        self.softwaretype = softwaretype
        self.stationtype = stationtype
        loginf("Data will be uploaded to %s" % self.server_url)
        self.skip_upload = to_bool(skip_upload)

    def format_url(self, record):
        """
        Build a GET URL with all observation data as query parameters.

        The v2 API authenticates via the PASSWORD query parameter and
        identifies the station via the 'id' query parameter.
        Observation time is sent as a POSIX timestamp via 'ts'.
        """
        record_m = weewx.units.to_METRICWX(record)

        params = {}

        # --- Authentication & station identity ---
        params['PASSWORD'] = self.password
        params['id'] = self.station

        # --- Observation time (POSIX seconds, preferred over dateutc) ---
        params['ts'] = int(record_m['dateTime'])

        # --- Meteorological fields ---
        # Temperature (°C preferred; API falls back to tempf if temp absent)
        if record_m.get('outTemp') is not None:
            params['temp'] = round(record_m['outTemp'], 2)

        # Wind speed (m/s preferred; API falls back to windspeedmph)
        if record_m.get('windSpeed') is not None:
            params['wind'] = round(record_m['windSpeed'], 2)

        # Wind gust (m/s preferred; API falls back to windgustmph)
        if record_m.get('windGust') is not None:
            params['gust'] = round(record_m['windGust'], 2)

        # Wind direction (degrees 0-360)
        if record_m.get('windDir') is not None:
            params['winddir'] = int(record_m['windDir'])

        # Humidity (%; 'humidity' preferred over legacy 'rh')
        if record_m.get('outHumidity') is not None:
            params['humidity'] = round(record_m['outHumidity'], 1)

        # Dew point (°C preferred; API falls back to dewptf)
        if record_m.get('dewpoint') is not None:
            params['dewpoint'] = round(record_m['dewpoint'], 2)

        # Pressure: API accepts 'pressure' (Pa), 'mbar' (hPa), or 'baromin' (inHg).
        # weewx METRICWX barometer is in hPa, so send as 'mbar'.
        if record_m.get('barometer') is not None:
            params['mbar'] = round(record_m['barometer'], 2)

        # Precipitation over the last hour (mm preferred; API falls back to rainin)
        if record_m.get('hourRain') is not None:
            params['precip'] = round(record_m['hourRain'], 2)

        # UV index ('uv' preferred over legacy 'UV')
        if record_m.get('UV') is not None:
            params['uv'] = round(record_m['UV'], 1)

        # Solar radiation (W/m²)
        if record_m.get('radiation') is not None:
            params['solarradiation'] = round(record_m['radiation'], 1)

        # Optional station metadata
        if self.softwaretype:
            params['softwaretype'] = self.softwaretype
        if self.stationtype:
            params['stationtype'] = self.stationtype

        url = '%s?%s' % (self.server_url, urlencode(params))

        if weewx.debug >= 2:
            # Mask the password in debug output
            safe_params = dict(params)
            safe_params['PASSWORD'] = '****'
            logdbg("url: %s?%s" % (self.server_url, urlencode(safe_params)))

        return url

    def get_post_body(self, record):
        """
        The v2 API uses GET (all data in the query string), so there is no
        POST body. Returning None signals the base class to use GET.
        """
        return None

    def send_request(self, req):
        """
        Override to send a plain GET request rather than a POST.
        The base RESTThread.send_request posts a body; we just need GET.
        """
        # req is a urllib Request object whose full_url already has params
        response = urlopen(req, timeout=self.timeout)
        return response

    def process_record(self, record, dbmanager):
        """
        Override process_record so we can build a GET request directly.
        The parent class builds a POST; we replace that with a GET.
        """
        url = self.format_url(record)

        if self.skip_upload:
            loginf("skip_upload is set; skipping upload")
            return

        req = Request(url)
        req.add_header('User-Agent', 'weewx/%s' % weewx.__version__)

        for count in range(self.max_tries):
            try:
                response = urlopen(req, timeout=self.timeout)
                code = response.getcode()
                if code == 200:
                    if self.log_success:
                        loginf("upload successful")
                    return
                elif code == 409:
                    # Duplicate request - not a real error, just skip
                    loginf("duplicate observation detected (409); skipping")
                    return
                else:
                    logerr("unexpected HTTP response: %s" % code)
            except Exception as e:
                logerr("upload failed (attempt %d of %d): %s"
                       % (count + 1, self.max_tries, e))
                if count + 1 < self.max_tries:
                    time.sleep(self.retry_wait)

        logerr("upload failed after %d attempts" % self.max_tries)


# Use this hook to test the uploader:
#   PYTHONPATH=bin python bin/user/windy.py

if __name__ == "__main__":
    weewx.debug = 2

    import weeutil.logger
    weeutil.logger.setup('windy', {})

    q = queue.Queue()
    t = WindyThread(q, password='my-station-password', station='pws-station-001',
                    softwaretype='weewx-%s' % weewx.__version__,
                    stationtype='Simulator')
    t.start()
    r = {'dateTime': int(time.time() + 0.5),
         'usUnits': weewx.US,
         'outTemp': 32.5,
         'inTemp': 75.8,
         'outHumidity': 24,
         'windSpeed': 10,
         'windDir': 32,
         'barometer': 29.92,
         'UV': 3.4}
    print(t.format_url(r))
    q.put(r)
    q.put(None)
    t.join(30)