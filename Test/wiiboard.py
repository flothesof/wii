'''Wiiboard driver -- pygame style
Nedim Jackman December 2008
No liability held for any use of this software.
More information at http://code.google.com/p/wiiboard-simple/
'''

import bluetooth
import thread
import time
from datetime import datetime
from Queue import Queue, Empty



CONTINUOUS_REPORTING = "04" #Easier as string with leading zero

COMMAND_LIGHT = 11
COMMAND_REPORTING = 12
COMMAND_REQUEST_STATUS = 15
COMMAND_REGISTER = 16
COMMAND_READ_REGISTER = 17

#input is Wii device to host
INPUT_STATUS = 20
INPUT_READ_DATA = 21

EXTENSION_8BYTES = 32
#end "hex" values

BUTTON_DOWN_MASK = 8

TOP_RIGHT = 0
BOTTOM_RIGHT = 1
TOP_LEFT = 2
BOTTOM_LEFT = 3

BLUETOOTH_NAME = "Nintendo RVL-WBC-01"


class BoardEvent:
    def __init__(self, topLeft,topRight,bottomLeft,bottomRight, buttonPressed, buttonReleased, time_stamp):

        self.topLeft = topLeft
        self.topRight = topRight
        self.bottomLeft = bottomLeft
        self.bottomRight = bottomRight
        self.buttonPressed = buttonPressed
        self.buttonReleased = buttonReleased
        #convenience value
        self.totalWeight = topLeft + topRight + bottomLeft + bottomRight
        self.time_stamp = time_stamp
        
    def print_form(self):
        return ",".join(map(str, [self.time_stamp, self.topLeft, self.topRight, 
                         self.bottomLeft, self.bottomRight,
                         self.totalWeight]))
    
    def spatial_coords(self, calibration_mass):
        R = self.topRight + self.bottomRight
        L = self.topLeft + self.bottomLeft
        T = self.topRight + self.topLeft
        B = self.bottomRight + self.bottomLeft
        return (215 * (R - L) / calibration_mass, 
                         117.5 * (T - B) / calibration_mass)
                         
class Wiiboard:


    def __init__(self):
        self.init_time = datetime.now()
        self.data_queue = Queue()
        self.queue_logging = False
        self.calibration = []
        self.calibrationRequested = False
        self.LED = False
        self.address = None
        self.buttonDown = False
        for i in xrange(3):
            self.calibration.append([])
            for j in xrange(4):
                self.calibration[i].append(10000) #high dummy value so events with it don't register
        
        self.status = "Disconnected"
        self.lastEvent = BoardEvent(0,0,0,0,False,False, 0)

    def isConnected(self):
        return self.status == "Connected"

    # Connect to the Wiiboard at bluetooth address <address>
    def connect(self, address):
        if address == None:
            print "Non existant address"
            return
        if self.status != "Disconnected":
            raise Exception("not disconnected: " + self.status)
        self.receivesocket = bluetooth.BluetoothSocket(bluetooth.L2CAP)
        self.controlsocket = bluetooth.BluetoothSocket(bluetooth.L2CAP)
        self.receivesocket.connect((address, 0x13))
        self.controlsocket.connect((address, 0x11))
        if self.receivesocket and self.controlsocket:
            print "Connected to board at address " + address
            self.status = "Connected"
            self.address = address
            thread.start_new_thread(self.receivethread, ())
            self.calibrate()
            useExt = ["00",COMMAND_REGISTER,"04","A4","00","40","00"]
            self.send(useExt)
            self.setReportingType()
        else:
            print "Could not connect to board at address " + address

    # Disconnect from the Wiiboard
    def disconnect(self):
        if self.status == "Connected":
            self.status = "Disconnecting"
            while self.status == "Disconnecting":
                self.wait(1)
        try:
            self.receivesocket.close()
            self.controlsocket.close()
            self.receivesocket = None
            self.controlsocket = None
        except:
            pass
        print "board disconnected"

    # Try to discover a Wiiboard
    def discover(self):
        print "Press the red sync button on the board now"
        for i in range(6):
            bluetoothdevices = bluetooth.discover_devices(duration = 1, lookup_names = True)
            for bluetoothdevice in bluetoothdevices:
                if bluetoothdevice[1] == BLUETOOTH_NAME:
                    address = bluetoothdevice[0]
                    print "Found board at address " + address
                    return address
        print "No boards discovered."
        return None

    def createBoardEvent(self, bytes):
        buttonBytes = bytes[0:2]
        bytes = bytes[2:12]
        buttonPressed = False
        buttonReleased = False

        state = (int(buttonBytes[0].encode("hex"),16) << 8 ) | int(buttonBytes[1].encode("hex"),16)
        if state == BUTTON_DOWN_MASK:
            buttonPressed = True
            if not self.buttonDown:
                self.buttonDown = True

        if buttonPressed == False:
            if self.lastEvent.buttonPressed == True:
                buttonReleased = True
                self.buttonDown = False

        rawTR = (int(bytes[0].encode("hex"),16) << 8 ) + int(bytes[1].encode("hex"),16)
        rawBR = (int(bytes[2].encode("hex"),16) << 8 ) + int(bytes[3].encode("hex"),16)
        rawTL = (int(bytes[4].encode("hex"),16) << 8 ) + int(bytes[5].encode("hex"),16)
        rawBL = (int(bytes[6].encode("hex"),16) << 8 ) + int(bytes[7].encode("hex"),16)

        topLeft = self.calcMass(rawTL, TOP_LEFT)
        topRight = self.calcMass(rawTR, TOP_RIGHT)
        bottomLeft = self.calcMass(rawBL, BOTTOM_LEFT)
        bottomRight = self.calcMass(rawBR, BOTTOM_RIGHT)
        boardEvent = BoardEvent(topLeft,topRight,bottomLeft,bottomRight,buttonPressed,buttonReleased, int((datetime.now() - self.init_time).total_seconds() * 1000000))
        return boardEvent


    def calcMass(self, raw, pos):
        #calibration[0] is calibration values for 0kg
        #calibration[1] is calibration values for 17kg
        #calibration[2] is calibration values for 34kg
        if raw < self.calibration[0][pos]:
            return 0.0
        elif raw < self.calibration[1][pos]:
            return 17 * ((raw - self.calibration[0][pos]) / float((self.calibration[1][pos] - self.calibration[0][pos])))
        else:
            return 17 + 17 * ((raw - self.calibration[1][pos]) / float((self.calibration[2][pos] - self.calibration[1][pos])))

    def getEvent(self):
        return self.lastEvent
        
    def getLED(self):
        return self.LED

    # Thread that listens for incoming data
    def receivethread(self):
        #try:
    #    self.receivesocket.settimeout(0.1)       #not for windows?
        while self.status == "Connected":
                data = self.receivesocket.recv(25)
                intype = int( data.encode("hex")[2:4] )
                if intype == INPUT_STATUS:
                    # Status input received. It just tells us battery life really
                    self.setReportingType()
                elif intype == INPUT_READ_DATA:
                    if self.calibrationRequested == True:
                        packetLength = (int(str(data[4]).encode("hex"),16)/16 + 1)
                        self.parseCalibrationResponse(data[7:(7+packetLength)])

                        if packetLength < 16:
                            self.calibrationRequested = False

                elif intype == EXTENSION_8BYTES:
                    event = self.createBoardEvent(data[2:12])
                    self.lastEvent = event
                    if self.queue_logging:
                        self.data_queue.put(event)

        self.status = "Disconnected"

    def parseCalibrationResponse(self, bytes):
        index = 0
        if len(bytes) == 16:
            for i in xrange(2):
                for j in xrange(4):
                    self.calibration[i][j] = (int(bytes[index].encode("hex"),16) << 8 ) + int(bytes[index+1].encode("hex"),16)
                    index += 2
        elif len(bytes) < 16:
            for i in xrange(4):
                self.calibration[2][i] = (int(bytes[index].encode("hex"),16) << 8 ) + int(bytes[index+1].encode("hex"),16)
                index += 2

    # Send <data> to the Wiiboard
    # <data> should be an array of strings, each string representing a single hex byte
    def send(self,data):
        if self.status != "Connected":
            return
        data[0] = "52"

        senddata = ""
        for byte in data:
            byte = str(byte)
            senddata += byte.decode("hex")

        self.controlsocket.send(senddata)

    #Turns the power button LED on if light is True, off if False
    #The board must be connected in order to set the light
    def setLight(self, light):
        val = "00"
        if light == True:
            val = "10"

        message = ["00", COMMAND_LIGHT, val]
        self.send(message)
        self.LED = light

    def calibrate(self):
        message = ["00", COMMAND_READ_REGISTER ,"04", "A4", "00", "24", "00", "18"]
        self.send(message)
        self.calibrationRequested = True

    def setReportingType(self):
        bytearr = ["00", COMMAND_REPORTING, CONTINUOUS_REPORTING, EXTENSION_8BYTES]
        self.send(bytearr)

    # Wait <millis> milliseconds
    def wait(self,millis):
        time.sleep(millis / 1000.0)

    def getQueuedEvents(self):
        queued_events = []
        try:
            while True:
                queued_events.append(self.data_queue.get_nowait())
        except Empty:
            return queued_events
        
if __name__ == '__main__':
    adr = "00:22:4C:55:A2:32"
    wb = Wiiboard()
    wb.connect(adr)
    