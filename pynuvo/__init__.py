#Modified pynuvo (Ileo19 fork) from pymonoprice

import asyncio
import functools
import logging
import re
import serial
import time  # Need this for synchornized
import string  # is this necessary? not in pyblackbird
import io  # is this necessary? not in pyblackbird
from functools import wraps
from serial_asyncio import create_serial_connection
from threading import RLock


_LOGGER = logging.getLogger(__name__)

'''
#Zx,ON,SRCs,VOLyy,DNDd,LOCKl<CR><LF>
'''
GRAND_CONCERTO_PWR_ON_PATTERN = re.compile('#Z(?P<zone>\d),'
                    '(?P<power>ON),'
                    'SRC(?P<source>\d),'
                    'VOL(?P<volume>\d\d),'
                    'DND(?P<dnd>\d),'
                    'LOCK(?P<lock>\d)')

'''
#Zx,OFF<CR><LF>
'''
GRAND_CONCERTO_PWR_OFF_PATTERN = re.compile('#Z(?P<zone>\d),'
                     '(?P<power>OFF)')

'''
#Zx,ON,SRCs,MUTE,DNDd,LOCKl<CR><LF>
'''
GRAND_CONCERTO_MUTE_PATTERN = re.compile('#Z(?P<zone>\d),'
                     '(?P<power>ON),'
                     'SRC(?P<source>\d),'
                     '(?P<volume>MUTE),'
                     'DND(?P<dnd>\d),'
                     'LOCK(?P<lock>\d)')



EOL = b'\r\n'
LEN_EOL = len(EOL)  # not in original pynuvo, but needed for async
TIMEOUT_OP       = 0.2   # Number of seconds before serial operation timeout, this is sig shorter than in blackbird which is 2.0
TIMEOUT_RESPONSE = 2.5   # Number of seconds before command response timeout
VOLUME_DEFAULT  = 79    # Value used when zone is muted or otherwise unable to get volume integer

class ZoneStatus(object):     # #Z1,ON,SRC4,VOL60,DND0,LOCK0 – POWER ON (page 7 of NUVO Protocol.pdf)
    def __init__(self
                 ,zone: int
                 ,power: str  # ON=Power is ON, OFF=Power is OFF
                 ,source: int = '1'  # 1 to 6, But zone 6 might be paging system
                 ,volume: int = '60'  # volume level: 0=Max to 79=Min
                 ,dnd: int = '0'    # 0=Do Not Disturb is OFF, 1=Do Not Disturb is ON
                 ,lock: int = '0'   # 0=Zone is not locked, 1=Zone is locked
                 ):
        self.zone = zone
        self.source = source

        _LOGGER.debug('zone - %s' , zone)
        _LOGGER.debug('power - %s' , power)
        _LOGGER.debug('source - %s' , source)
        _LOGGER.debug('volume - %s', volume)
        if 'ON' in power:  # change str to a boolean value to work correctly in HASS
           self.power = bool(1)
        else:
           self.power = bool(0)
#        self.sourcename = ''
#        self.treble = treble
#        self.bass = bass
        if 'MUTE' in volume:
           self.mute = bool(1)
           self.volume = int(VOLUME_DEFAULT)
        else:
           self.mute = bool(0)
           self.volume = int(volume)
#        self.treble = 0
#        self.bass = 0

    @classmethod
    def from_string(cls, string: bytes):
        if not string:
            return None
        _LOGGER.debug('string passed to ZoneStatus.from_string - %s' , string)

        match = _parse_response(string)
        
        if not match:
            return None

        try:
        #    _LOGGER.debug('match.groups =- %s' , match.groups())
           rtn = ZoneStatus(*[str(m) for m in match.groups()])
           #rtn = ZoneStatus(match.groups())
        except:
           rtn = None
        # _LOGGER.debug('ZoneStatus rtn - %s' , rtn)
        return rtn


class Nuvo(object):
    """
    Nuvo amplifier interface
    """

    def zone_status(self, zone: int):
        """
        Get the structure representing the status of the zone
        :param zone: zone 1.12
        :return: status of the zone or None
        """
        raise NotImplemented()

    def set_power(self, zone: int, power: bool):
        """
        Turn zone on or off
        :param zone: zone 1.12        
        :param power: True to turn on, False to turn off
        """
        raise NotImplemented()

    def set_all_zones(self, power:bool):
        """
        Turn all zones off
        :param power: True to turn ALL ZONES off
        """
        raise NotImplemented()

    def set_mute(self, zone: int, mute: bool):
        """
        Mute zone on or off
        :param zone: zone 1.12        
        :param mute: True to mute, False to unmute
        """
        raise NotImplemented()

    def set_volume(self, zone: int, volume: int):
        """
        Set volume for zone
        :param zone: zone 1.12        
        :param volume: integer from 0 to 79 inclusive
        """
        raise NotImplemented()

    def set_volume_up(self, zone: int):
        """
        Increment the Zones volume
        :param zone: zone 1.12        
        """
        raise NotImplemented()

    def set_volume_down(self, zone: int):
        """
        Decrement the Zones volume
        :param zone: zone 1.12        
        """
        raise NotImplemented()

    def set_treble(self, zone: int, treble: float):
        """
        Set treble for zone
        :param zone: zone 1.12        
        :param treble: float from -18 to 18 inclusive
        """
        raise NotImplemented()

    def set_bass(self, zone: int, bass: int):
        """
        Set bass for zone
        :param zone: zone 1.12        
        :param bass: float from -18 to 18 inclusive 
        """
        raise NotImplemented()

    def set_source(self, zone: int, source: int):
        """
        Set source for zone
        :param zone: zone 1.6        
        :param source: integer from 1 to 6 inclusive
        """
        raise NotImplemented()

    def restore_zone(self, status: ZoneStatus):
        """
        Restores zone to it's previous state
        :param status: zone state to restore
        """
        raise NotImplemented()


# Helpers

# def _is_int(s):
#     try: 
#         int(s)
#         return True
#     except ValueError:
#         return False

def _parse_response(string: bytes):
   """
   :param request: request that is sent to the nuvo
   :return: regular expression return match(s) 
   """
   match = re.search(GRAND_CONCERTO_PWR_ON_PATTERN, string)
   if match:
      _LOGGER.debug('GRAND_CONCERTO_PWR_ON_PATTERN - Match - %s', match)
      return match

   if not match:
      match = re.search(GRAND_CONCERTO_PWR_OFF_PATTERN, string)
      if match:
        _LOGGER.debug('GRAND_CONCERTO_PWR_OFF_PATTERN - Match - %s', match)
        return match

   if not match:
      match = re.search(GRAND_CONCERTO_MUTE_PATTERN, string)
      if match:
        _LOGGER.debug('GRAND_CONCERTO_MUTE_PATTERN - Match - %s', match)
        return match

   if not match:
       _LOGGER.debug('NO MATCH - %s' , string)
   return None

def _format_zone_status_request(zone: int) -> str:
    return 'Z{}STATUS?'.format(zone)

def _format_set_power(zone: int, power: bool) -> str:
    zone = int(zone)
    if (power):
       return 'Z{}ON'.format(zone) 
    else:
       return 'Z{}OFF'.format(zone)

def _format_set_mute(zone: int, mute: bool) -> str:
    if (mute):
       return 'Z{}MUTE'.format(int(zone))
    else:
       return 'Z{}MUTEOFF'.format(int(zone))

def _format_set_volume(zone: int, volume: int) -> str:
    #CMD *ZzVOLx  where "z" is Zone number and "x" is volume to use: 0=Max to 79=Min
    return 'Z{}VOL{:0=2}'.format(int(zone),volume)

def _format_set_volume_up(zone: int) -> str:
    #CMD *ZzVOL+  where "z" is the Zone number
    return 'Z{}VOL+'.format(int(zone))

def _format_set_volume_down(zone: int) -> str:
    #CMD *ZzVOL-   where "z" is the Zone number
    return 'Z{}VOL-'.format(int(zone))

def _format_set_treble(zone: int, treble: int) -> bytes:
    treble = int(max(12, min(treble, -12)))
    return 'Z{}TREB{:0=2}'.format(int(zone),treble)

def _format_set_bass(zone: int, bass: int) -> bytes:
    bass = int(max(12, min(bass, -12)))
    return 'Z{}BASS{:0=2}'.format(int(zone),bass)

def _format_set_source(zone: int, source: int) -> str:
    source = int(max(1, min(int(source), 6)))
    return 'Z{}SRC{}'.format(int(zone),source)



def get_nuvo(port_url):
    """
    Return synchronous version of Nuvo interface
    :param port_url: serial port, i.e. '/dev/ttyUSB0,/dev/ttyS0'
    :return: synchronous implementation of Nuvo interface
    """

    lock = RLock()

    def synchronized(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with lock:
                return func(*args, **kwargs)
        return wrapper

    class NuvoSync(Nuvo):
        def __init__(self, port_url):
            _LOGGER.info('Attempting connection - "%s"', port_url)
            self._port = serial.serial_for_url(port_url, do_not_open=True)
            self._port.baudrate = 57600
            self._port.stopbits = serial.STOPBITS_ONE
            self._port.bytesize = serial.EIGHTBITS
            self._port.parity = serial.PARITY_NONE
            self._port.timeout = TIMEOUT_OP
            self._port.write_timeout = TIMEOUT_OP
            self._port.open()


        def _process_request(self, request: str):
            """
            Send data to serial
            :param request: request that is sent ot the Nuvo
            :return: ascii string returned by Nuvo
            """

            # clear the port
            self._port.reset_output_buffer()
            self._port.reset_input_buffer()

            # send request
            #format and send output command
            lineout = "*" + request + "\r"
            self._port.write(lineout.encode())
            self._port.flush()
            _LOGGER.debug('Sending "%s"', lineout)

            # receive response
            result = bytearray()
            while True:
                c = self._port.read(1)
                if c is None:
                    break
                if not c:
                    raise serial.SerialTimeoutException(
                        'Connection timed out! Last received bytes {}'.format([hex(a) for a in result]))
                result += c
                if result [-LEN_EOL:] == EOL:
                    break
            ret = bytes (result)
            _LOGGER.debug('Received "%s"', ret)
            return ret.decode('ascii')
            

        @synchronized
        def zone_status(self, zone: int):
            # Returns status of the zone
            return ZoneStatus.from_string(self._process_request(_format_zone_status_request(zone)))

        @synchronized
        def set_power(self, zone: int, power: bool):
            # Set zone power
            self._process_request(_format_set_power(zone, power))
            
        @synchronized
        def set_mute(self, zone: int, mute: bool):
            # Mute the zone
            self._process_request(_format_set_mute(zone, mute))

        @synchronized
        def set_volume(self, zone: int, volume: int):
            # set volume of the zone
            self._process_request(_format_set_volume(zone, volume))

        @synchronized
        def set_volume_up(self, zone: int):
            # increase the volume by 1
            self._process_request(_format_set_volume_up(zone))

        @synchronized
        def set_volume_down(self, zone: int):
            # decrease the volume by 1
            self._process_request(_format_set_volume_down(zone))

        @synchronized
        def set_treble(self, zone: int, treble: float):
            # set the treble of the zone
            self._process_request(_format_set_treble(zone, treble))

        @synchronized
        def set_bass(self, zone: int, bass: float):
            # set the bass of the zone
            self._process_request(_format_set_bass(zone, bass))

        @synchronized
        def set_source(self, zone: int, source: int):
            # set the source of the zone
            self._process_request(_format_set_source(zone, source))

        @synchronized
        def restore_zone(self, status: ZoneStatus):
            self.set_power(status.zone, status.power)
            self.set_mute(status.zone, status.mute)
            self.set_volume(status.zone, status.volume)
            self.set_source(status.zone, status.source)
            self.set_treble(status.zone, status.treble)
            self.set_bass(status.zone, status.bass)

    return NuvoSync(port_url)
  

@asyncio.coroutine
def get_async_nuvo(port_url, loop):
    """
    Return asynchronous version of Nuvo interface
    :param port_url: serial port, i.e. '/dev/ttyUSB0'
    :return: asynchronous implementation of Nuvo interface
    """

    lock = asyncio.Lock()

    def locked_coro(coro):
        @asyncio.coroutine
        @wraps(coro)
        def wrapper(*args, **kwargs):
            with (yield from lock):
                return (yield from coro(*args, **kwargs))
        return wrapper

    class NuvoAsync(Nuvo):
        def __init__(self, nuvo_protocol):
            self._protocol = nuvo_protocol

        @locked_coro
        @asyncio.coroutine
        def zone_status(self, zone: int):
            string = yield from self._protocol.send(_format_zone_status_request(zone))
            return ZoneStatus.from_string(string)

        @locked_coro
        @asyncio.coroutine
        def set_power(self, zone: int, power: bool):
            yield from self._protocol.send(_format_set_power(zone, power))

        @locked_coro
        @asyncio.coroutine
        def set_mute(self, zone: int, mute: bool):
            yield from self._protocol.send(_format_set_mute(zone, mute))

        @locked_coro
        @asyncio.coroutine
        def set_volume(self, zone: int, volume: int):
            yield from self._protocol.send(_format_set_volume(zone, volume))

        @locked_coro
        @asyncio.coroutine
        def set_volume_up(self, zone: int):
            yield from self._protocol.send(_format_set_volume_up(zone))

        @locked_coro
        @asyncio.coroutine
        def set_volume_down(self, zone: int):
            yield from self._protocol.send(_format_set_volume_down(zone))

        @locked_coro
        @asyncio.coroutine
        def set_treble(self, zone: int, treble: float):
            yield from self._protocol.send(_format_set_treble(zone, treble))

        @locked_coro
        @asyncio.coroutine
        def set_bass(self, zone: int, bass: float):
            yield from self._protocol.send(_format_set_bass(zone, bass))

        @locked_coro
        @asyncio.coroutine
        def set_source(self, zone: int, source: int):
            yield from self._protocol.send(_format_set_source(zone, source))

        @locked_coro
        @asyncio.coroutine
        def restore_zone(self, status: ZoneStatus):
            yield from self._protocol.send(_format_set_power(status.zone, status.power))
            yield from self._protocol.send(_format_set_mute(status.zone, status.mute))
            yield from self._protocol.send(_format_set_volume(status.zone, status.volume))
            yield from self._protocol.send(_format_set_source(status.zone, status.source))
            yield from self._protocol.send(_format_set_treble(status.zone, status.treble))
            yield from self._protocol.send(_format_set_bass(status.zone, status.bass))

    class NuvoProtocol(asyncio.Protocol):
        def __init__(self, loop):
            super().__init__()
            self._loop = loop
            self._lock = asyncio.Lock()
            self._transport = None
            self._connected = asyncio.Event(loop=loop)
            self.q = asyncio.Queue(loop=loop)

        def connection_made(self, transport):
            self._transport = transport
            self._connected.set()
            _LOGGER.debug('port opened %s', self._transport)

        def data_received(self, data):
            asyncio.ensure_future(self.q.put(data), loop=self._loop)

        @asyncio.coroutine
        def send(self, request: bytes, skip=0):
            yield from self._connected.wait()
            result = bytearray()
            # Only one transaction at a time
            with (yield from self._lock):
                self._transport.serial.reset_output_buffer()
                self._transport.serial.reset_input_buffer()
                while not self.q.empty():
                    self.q.get_nowait()
                self._transport.write(request)
                try:
                    while True:
                        result += yield from asyncio.wait_for(self.q.get(), TIMEOUT_OP, loop=self._loop)
                        if len(result) > skip and result[-LEN_EOL:] == EOL:
                            ret = bytes(result)
                            _LOGGER.debug('Received "%s"', ret)
                            return ret.decode('ascii')
                except asyncio.TimeoutError:
                    _LOGGER.error("Timeout during receiving response for command '%s', received='%s'", request, result)
                    raise

    _, protocol = yield from create_serial_connection(loop, functools.partial(NuvoProtocol, loop),
                                                      port_url, baudrate=57600)
    return NuvoAsync(protocol)
