from XPLMDataAccess import *
from XPLMUtilities  import *

class EasyDref:
    '''
    Easy Dataref access

    Copyright (C) 2011  Joan Perez i Cauhe
    '''

    datarefs = []

    def __init__(self, dataref, type = "float", register = False, writable = False):
        # Clear dataref
        dataref = dataref.strip()
        self.isarray, dref = False, False

        if ('"' in dataref):
            dref = dataref.split('"')[1]
            dataref = dataref[dataref.rfind('"')+1:]

        if ('(' in dataref):
            # Detect embedded type, and strip it from dataref
            type = dataref[dataref.find('(')+1:dataref.find(')')]
            dataref = dataref[:dataref.find('(')] + dataref[dataref.find(')')+1:]

        if ('[' in dataref):
            # We have an array
            self.isarray = True
            range = dataref[dataref.find('[')+1:dataref.find(']')].split(':')
            dataref = dataref[:dataref.find('[')]
            if (len(range) < 2):
                range.append(range[0])

            self.initArrayDref(range[0], range[1], type)

        elif (type == "int"):
            self.dr_get = XPLMGetDatai
            self.dr_set = XPLMSetDatai
            self.dr_type = xplmType_Int
            self.cast = int
        elif (type == "float"):
            self.dr_get = XPLMGetDataf
            self.dr_set = XPLMSetDataf
            self.dr_type = xplmType_Float
            self.cast = float
        elif (type == "double"):
            self.dr_get = XPLMGetDatad
            self.dr_set = XPLMSetDatad
            self.dr_type = xplmType_Double
            self.cast = float
        else:
            print "ERROR: invalid DataRef type", type

        if dref: dataref = dref

        if register:
            if writable:
                self.setCB = self.set_f
            else:
                self.setCB = False
            self.getCB = self.get_f

            self.DataRef = XPLMRegisterDataAccessor(self, dataref, self.dr_type,
            writable, self.getCB, self.setCB, self.getCB, self.setCB, self.getCB, self.setCB
            , self.getCB, self.setCB, self.getCB, self.setCB, self.getCB, self.setCB,
            False, False)

            self.__class__.datarefs.append(self.DataRef)

            # Init default value
            if self.isarray:
                self.value_f = [0] * self.count
            else:
                self.value_f = 0

            # Local shortcut
            self.set = self.set_f
            self.get = self.get_f

        else:
            self.DataRef = XPLMFindDataRef(dataref)
            if self.DataRef == False:
                print "Can't find " + dataref + " DataRef"

    def initArrayDref(self, first, last, type):
        self.index = int(first)
        self.count = int(last) - int(first) +1
        self.last = int(last)

        if (type == "int"):
            self.rget = XPLMGetDatavi
            self.rset = XPLMSetDatavi
            self.dr_type = xplmType_IntArray
            self.cast = int
        elif (type == "float"):
            self.rget = XPLMGetDatavf
            self.rset = XPLMSetDatavf
            self.dr_type = xplmType_FloatArray
            self.cast = float
        elif (type == "bit"):
            self.rget = XPLMGetDatab
            self.rset = XPLMSetDatab
            self.dr_type = xplmType_DataArray
            self.cast = float
        else:
            print "ERROR: invalid DataRef type", type
        pass

    def set(self, value):
        if (self.isarray):
            self.rset(self.DataRef, value, self.index, len(value))
        else:
            self.dr_set(self.DataRef, self.cast(value))

    def get(self):
        if (self.isarray):
            list = []
            self.rget(self.DataRef, list, self.index, self.count)
            return list
        else:
            return self.dr_get(self.DataRef)

    def set_f(self, value):
        self.value_f = value

    def get_f(self):
        return self.value_f

    def __getattr__(self, name):
        if name == 'value':
            return self.get()
        else:
            raise AttributeError

    def __setattr__(self, name, value):
        if name == 'value':
            self.set(value)
        else:
            self.__dict__[name] = value

    @classmethod
    def cleanup(cls):
        for dataref in cls.datarefs:
            XPLMUnregisterDataAccessor(dataref)

class EasyCommand:
    '''
    Creates a command with an assigned callback with arguments
    '''
    def __init__(self, plugin, command, function, args = False, description =''):
        command = 'xjpc/XPNoaaWeather/' + command
        self.command = XPLMCreateCommand(command, description)
        self.commandCH = self.commandCHandler
        XPLMRegisterCommandHandler(plugin, self.command, self.commandCH, 1, 0)

        self.function = function
        self.args = args
        self.plugin = plugin
        # Command handlers
    def commandCHandler(self, inCommand, inPhase, inRefcon):
        if inPhase == 0:
            if self.args:
                if type(self.args).__name__ == 'tuple':
                    self.function(*self.args)
                else:
                    self.function(self.args)
            else:
                self.function()
        return 0
    def destroy(self):
        XPLMUnregisterCommandHandler(self.plugin, self.command, self.commandCH, 1, 0)
