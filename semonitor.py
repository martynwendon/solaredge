#!/usr/bin/python

# SolarEdge inverter performance monitoring using the SolarEdge protocol

import time
import threading
from seConf import *
from seFiles import *
from seMsg import *
from seData import *
from seCommands import *

# global variables
threadLock = threading.Lock()       # lock to synchronize reads and writes
masterEvent = threading.Event()     # event to signal RS485 master release
running = True
dataInSeq = 0
dataOutSeq = 0
outSeq = 0

# process the input data
def readData(dataFile, recFile, outFile):
    global dataInSeq, dataOutSeq
    if updateFileName != "":    # create an array of zeros for the firmware update file
        updateBuf = list('\x00'*updateSize)
    if passiveMode:
        (msg, dataInSeq) = readMsg(dataFile, dataInSeq, recFile)   # skip data until the start of the first complete message
    while running:
        (msg, dataInSeq) = readMsg(dataFile, dataInSeq, recFile)
        if msg == "":   # end of file
            # eof from network means connection was broken, wait for a reconnect and continue
            if networkDevice:
                closeData(dataFile)
                dataFile = openDataSocket()
            else: # all finished
                if updateFileName != "":    # write the firmware update file
                    writeUpdate()
                return
        if msg == "\x00"*len(msg):   # ignore messages containing all zeros
            if debugData: logData(msg)
        else:
            with threadLock:
                try:
                    processMsg(msg, dataFile, recFile, outFile)
                except Exception as ex:
                    debug("debugEnable", "Exception:", ex.args[0])
                    if haltOnException:
                        logData(msg)
                        raise

# process a received message
def processMsg(msg, dataFile, recFile, outFile):
    global dataInSeq, dataOutSeq, outSeq
    # parse the message
    (msgSeq, fromAddr, toAddr, function, data) = parseMsg(msg)
    msgData = parseData(function, data)                    
    if (function == PROT_CMD_SERVER_POST_DATA) and (data != ""):    # performance data
        # write performance data to output files
        outSeq = writeData(msgData, outFile, outSeq)
    elif (updateFileName != "") and function == PROT_CMD_UPGRADE_WRITE:    # firmware update data
        updateBuf[msgData["offset"]:msgData["offset"]+msgData["length"]] = msgData["data"]
    if (networkDevice or masterMode):    # send reply
        replyFunction = ""
        if function == PROT_CMD_SERVER_POST_DATA:      # performance data
            # send ack
            replyFunction = PROT_RESP_ACK
            replyData = ""
        elif function == PROT_CMD_SERVER_GET_GMT:    # time request
            # set time
            replyFunction = PROT_RESP_SERVER_GMT
            replyData = formatTime(int(time.time()), (time.localtime().tm_hour-time.gmtime().tm_hour)*60*60)
        elif function == PROT_RESP_POLESTAR_MASTER_GRANT_ACK:   # RS485 master release
            masterEvent.set()
        if replyFunction != "":
            msg = formatMsg(msgSeq, toAddr, fromAddr, replyFunction, replyData)
            dataOutSeq = sendMsg(dataFile, msg, dataOutSeq, recFile)

# write firmware image to file
def writeUpdate():
    updateBuf = "".join(updateBuf)
    debug("debugFiles", "writing", updateFileName)
    with open(updateFileName, "w") as updateFile:
        updateFile.write(updateBuf)

# RS485 master commands thread
def masterCommands(dataFile, recFile):
    global dataOutSeq
    while running:
        for slaveAddr in slaveAddrs:
            with threadLock:
                # grant control of the bus to the slave
                dataOutSeq = sendMsg(dataFile, formatMsg(nextSeq(), masterAddr, int(slaveAddr, 16), PROT_CMD_POLESTAR_MASTER_GRANT), dataOutSeq, recFile)
            # wait for slave to release the bus
            masterEvent.clear()
            masterEvent.wait()
        time.sleep(masterMsgInterval)

# perform the specified commands
def doCommands(dataFile, commands, recFile):
    global dataInSeq, dataOutSeq, outSeq
    slaveAddr = int(slaveAddrs[0], 16)
    for command in commands:
        # format the command parameters
        function = int(command[0],16)
        format = "<"+"".join(c[0] for c in command[1:])
        params = [int(p[1:],16) for p in command[1:]]
        # send the command
        dataOutSeq = sendMsg(dataFile, formatMsg(nextSeq(), masterAddr, slaveAddr, function, struct.pack(format, *tuple(params))), dataOutSeq, recFile)
        # wait for the response
        (msg, dataInSeq) = readMsg(dataFile, dataInSeq, recFile)
        (msgSeq, fromAddr, toAddr, function, data) = parseMsg(msg)
        msgData = parseData(function, data, int(command[0],16))
	outSeq = writeData(msgData, outFile, outSeq)
        # wait a bit before sending the next one                    
        time.sleep(commandDelay)

if __name__ == "__main__":
    # initialization
    dataFile = openData(inFileName)
    (recFile, outFile) = openOutFiles(recFileName, outFileName)
    if passiveMode: # only reading from file or serial device
        # read until eof then terminate
        readData(dataFile, recFile, outFile)
    else:   # reading and writing to network or serial device
        if commandAction:   # commands were specified
            # perform commands then terminate
            doCommands(dataFile, commands, recFile)
        else:   # network or RS485
            # start a thread for reading
            readThread = threading.Thread(name=readThreadName, target=readData, args=(dataFile, recFile, outFile))
            readThread.start()
            debug("debugFiles", "starting", readThreadName)
            if masterMode:  # send RS485 master commands
                # start a thread to poll for data
                masterThread = threading.Thread(name=masterThreadName, target=masterCommands, args=(dataFile, recFile))
                masterThread.start()
                debug("debugFiles", "starting", masterThreadName)
            # wait for termination
            running = waitForEnd()
    # cleanup
    closeData(dataFile)
    closeOutFiles(recFile, outFile)
    
