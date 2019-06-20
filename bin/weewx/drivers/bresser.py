#
#    Copyright (c) 2019 Simon Bichler <bichlesi@web.de>
#
"""USB driver for Bresser 6-in-1 PC Weather Station for the weewx weather system
https://www.bresser.de/en/Weather-Time/BRESSER-PC-Weather-station-with-6-in-1-outdoor-sensor.html
"""
import weeutil.weeutil
import weewx.drivers
import time
import usb
import syslog
from datetime import datetime
import pprint

DRIVER_NAME = 'BresserUSB'
DRIVER_VERSION = "0.1"

def loader(config_dict, engine):
  return BresserUSB(**config_dict[DRIVER_NAME])

def configurator_loader(config_dict):
    return BresserUSBConfigurator()

def confeditor_loader():
    return BresserUSBConfEditor()

class BresserUSBConfEditor(weewx.drivers.AbstractConfEditor):
  @property
  def default_stanza(self):
    return """
[BresserUSB]
    # This section is for the Bresser 6-in-1 weather station.
    # The driver to use:
    driver = weewx.drivers.bresser
"""

  def get_conf(self, orig_stanza=None):
    if orig_stanza is None:
      return self.default_stanza
    return orig_stanza

class BresserUSBConfigurator(weewx.drivers.AbstractConfigurator):
  def add_options(self, parser):
    super(BresserUSBConfigurator, self).add_options(parser)
    parser.add_option("--live", dest="live", action="store_true", help="display live readings from the station")
    parser.add_option("--set-time", dest="set_time", action="store_true", help="Set station time to match computer time")

  def do_options(self, options, parser, config_dict, prompt):
    self.station = BresserUSB(**config_dict[DRIVER_NAME])
    if options.live:
      self.show_live()
    elif options.set_time:
      self.set_time()
    self.station.closePort()

  def show_live(self):
    pp = pprint.PrettyPrinter(indent=2)
    for packet in self.station.genLoopPackets():
      pp.pprint(packet)

  def set_time(self):
    self.station.setTime()

def logmsg(level, msg):
  #syslog.syslog(level, 'fousb: %s' % msg)
  print '%s: bresserusb: %s' % (level, msg)

def logdbg(msg):
  logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
  logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
  logmsg(syslog.LOG_ERR, msg)

def logcrt(msg):
  logmsg(syslog.LOG_CRIT, msg)

class BresserUSB(weewx.drivers.AbstractDevice):
  """Driver for Bresser 6-in-1 USB stations."""

  def __init__(self, **stn_dict) :
    """Initialize the station object."""
    self.vendor_id     = 0x1941
    self.product_id    = 0x8021
    self.usb_interface = 0
    self.usb_endpoint  = 0x81
    self.usb_read_size = 64
    self.loop_interval = 15
    self.openport_interval = 15
    self.timeout       = float(stn_dict.get('timeout', 15))
    self.debug         = int(stn_dict.get('debug', 0))
    self.the_time      = time.time()
    loginf('driver version is %s' % DRIVER_VERSION)
    self.devh = None
    self.openPort()
    self.setTime()

  def openPort(self):
    if self.devh is not None:
      return

    current_time = time.time()
    dev = self._find_device()
    while not dev:
      logerr("Cannot find USB device with Vendor=0x%04x ProdID=0x%04x" % (self.vendor_id, self.product_id))
      sleep_time = current_time + self.openport_interval - time.time()
      if sleep_time > 0:
        time.sleep(sleep_time)
      current_time += self.openport_interval
      dev = self._find_device()

    self.devh = dev.open()
    if not self.devh:
      raise weewx.WeeWxIOError("Open USB device failed")

    # be sure kernel does not claim the interface
    try:
      self.devh.detachKernelDriver(self.usb_interface)
    except:
      pass

    # attempt to claim the interface
    try:
      self.devh.claimInterface(self.usb_interface)
    except usb.USBError as e:
      self.closePort()
      logcrt("Unable to claim USB interface %s: %s" % (self.usb_interface, e))
      raise weewx.WeeWxIOError(e)

  def closePort(self):
    try:
      self.devh.releaseInterface()
    except:
      pass
    self.devh = None

  def _find_device(self):
    """Find the vendor and product ID on the USB."""
    for bus in usb.busses():
      for dev in bus.devices:
        if dev.idVendor == self.vendor_id and dev.idProduct == self.product_id:
          loginf('found station with Vendor=0x%04x ProdID=0x%04x on USB bus=%03i device=%03i' % (self.vendor_id, self.product_id, bus.location, dev.devnum))
          return dev
    return None

  @property
  def hardware_name(self):
    return "Bresser 6-in-1"

  def setTime(self):
    now = datetime.now()
    date = now.date()
    time = now.time()
    try:
      self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE, 0x0000009,
          [0xfc, 0x08, date.year-2000, date.month, date.day, 0x00, 0x00, 0xfd],
           0x0000200, 0x0000000, 1000)
      self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE, 0x0000009,
          [0xfc, 0x09, time.hour, time.minute, time.second, 0x00, 0x00, 0xfd],
           0x0000200, 0x0000000, 1000)
    except e:
        logerr("Failed to set station time to %s: %s" % (now.strftime("%Y-%m-%d %H:%M:%S"), e))
    loginf("Set station time to %s" % now.strftime("%Y-%m-%d %H:%M:%S"))

  def genLoopPackets(self):
    while True:
      try:
        dataset=self.read_usb_dataset()
      except usb.core.USBError as e:
        logerr("Lost USB connection.: %s" % e)
        self.closePort()
        self.openPort()
        self.the_time = time.time()
        continue
        
      ds=dataset.split()
      #2 2019-06-18 23:33 25.4 58 19.5 69 0.0 0.0 3.6 3.6 253 WSW 1014 953 0 13.6 --.- --.- -- --.- -- --.- -- --.- -- --.- -- --.- -- --.- --
      if len(ds) != 32:
        logerr("Got dataset with %i entries (instead of 32): '%s'" % (len(ds), dataset))
        continue
      ds_index = ds[0]
      ds_date  = ds[1]
      ds_time  = ds[2]
      computer_now = datetime.now()
      station_now = computer_now
      try:
        station_now = datetime.strptime(ds_date + " " + ds_time, "%Y-%m-%d %H:%M")
      except ValueError as e:
        logerr("Got dataset with invalid time: %s" % e)
        continue
      deltat = (computer_now-station_now)
      if deltat.total_seconds() > 70 or deltat.total_seconds() < -10:
        loginf("Delta too big: %i (Computer: %s, Station: %s)" % (deltat.total_seconds(), computer_now, station_now))
        self.setTime()
  
      #2 2019-06-18 23:33 25.4 58 19.5 69 0.0 0.0 3.6 3.6 253 WSW 1014 953 0 13.6 --.- --.- -- --.- -- --.- -- --.- -- --.- -- --.- -- --.- --
      _packet = {
        'dateTime'    : int(self.the_time+0.5), 
        'inTemp'      : ds[3], #Celsius
        'inHumidity'  : ds[4], #%REL
        'outTemp'     : ds[5], #Celsius
        'outHumidity' : ds[6], #%REL
        'rain'        : ds[7], #mmHourly
        'rainDaily'   : ds[8], #mmDaily
        'windSpeed'   : ds[9], #km/h
        'windGust'    : ds[10], #km/h
        'windDir'     : ds[11], #degrees
        'windCardinal': ds[12], #N,NNE,NE,ENE,E,ESE,SE,SSE,S,SSW,SW,WSW,W,WNW,NW,NNW
        'pressure'    : ds[13], #mBar/hPa
        'pressureAbs' : ds[14], #mBar/hPa
        'UV'          : ds[15], #"index"
        'dewPoint'    : ds[16], #Celsius
        'heatIndex'   : ds[17], #Celsius
        'ch1Temp  '   : ds[18], #Celsius
        'ch1Humidity' : ds[19], #mBar/hPa
        'ch2Temp  '   : ds[20], #Celsius
        'ch2Humidity' : ds[21], #mBar/hPa
        'ch3Temp  '   : ds[22], #Celsius
        'ch3Humidity' : ds[23], #mBar/hPa
        'ch4Temp  '   : ds[24], #Celsius
        'ch4Humidity' : ds[25], #mBar/hPa
        'ch5Temp  '   : ds[26], #Celsius
        'ch5Humidity' : ds[27], #mBar/hPa
        'ch6Temp  '   : ds[28], #Celsius
        'ch6Humidity' : ds[29], #mBar/hPa
        'ch7Temp  '   : ds[30], #Celsius
        'ch7Humidity' : ds[31], #mBar/hPa
        'usUnits' : weewx.US
      }

      #TODO: Prevent cumulative wait 
      yield _packet
      sleep_time = self.the_time + self.loop_interval - time.time()
      if sleep_time > 0:
        time.sleep(sleep_time)
        self.the_time += self.loop_interval
      else:
        self.the_time = time.time()

  def read_usb_dataset(self):
    dataset = ""
    response = self.read_usb_block("d5")
    packettype = response[0]
    packetcount= response[5]
    packetlen  = response[6]
    #print "0x%02X / 0x%02X (%i)\n" % (packettype, packetcount, packetlen)
    
    while packetcount != 0x31:
      response = self.read_usb_block("d5")
      packettype = response[0]
      packetcount= response[5]
      packetlen  = response[6]
      #print "0x%02X / 0x%02X (%i)\n" % (packettype, packetcount, packetlen)
    
    while packetcount in [0x31, 0x32, 0x33]:
      dataset += "".join(map(chr, response[7:7+packetlen]))
      response = self.read_usb_block("d5")
      packettype = response[0]
      packetcount= response[5]
      packetlen  = response[6]
      #print "0x%02X / 0x%02X (%i)\n" % (packettype, packetcount, packetlen)
    return dataset

  def read_usb_block(self, requesttype):
    #fc07000000e550fd => First request, no response
    #fc030000002fa1fd => Second request, same response as d5
    #fc0813051de417fd => Set Date to 19 05 29
    #fc09011e1cfb83fd => Set time to 2019-05-29 01:30:28
    #fc09021e0ff9f8fd => Set time to 2019-05-29 02:30:15
    #fc090c00078c42fd => Set time to 2019-05-29 12:00:07
    #fcd4010000e1bffd => 'normal' data request
    #fcd4020000b8effd => in case of f1 response
    #fcd5010000970bfd => 'normal' data request
    #fcd5020000ce5bfd => in case of f1 response
    requestbytes = {
      "d4": [0xfc, 0xd4, 0x01, 0x00, 0x00, 0xe1, 0xbf, 0xfd],
      "d42":[0xfc, 0xd4, 0x02, 0x00, 0x00, 0xb8, 0xef, 0xfd],
      "d5": [0xfc, 0xd5, 0x01, 0x00, 0x00, 0x97, 0x0b, 0xfd],
      "d52":[0xfc, 0xd5, 0x02, 0x00, 0x00, 0xce, 0x5b, 0xfd]
    }
    self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                         0x0000009,
                         requestbytes[requesttype],
                         0x0000200,
                         0x0000000,
                         1000)
    data = self.devh.interruptRead(self.usb_endpoint,
                                   self.usb_read_size, # bytes to read
                                   int(self.timeout*1000))
    return list(data)
