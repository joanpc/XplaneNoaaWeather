from math import hypot, atan2, degrees, exp, log

class c:
    '''
    Conversion tools
    '''
    #transition references
    transrefs = {}
    
    @classmethod
    def ms2knots(self, val):
        return val * 1.94384
    
    @classmethod
    def kel2cel(self, val):
        return val - 273.15
    
    @classmethod
    def c2p(self, x, y):
        #Cartesian 2 polar conversion
        r = hypot(x, y)
        a = degrees(atan2(x, y))
        if a < 0:
            a += 360
        if a <= 180:
            a = a + 180
        else:
            a = a -180
        return a, r
    
    @classmethod
    def mb2alt(self, mb):
        altpress = (1 - (mb/1013.25)**0.190284) * 44307
        return altpress
    @classmethod
    def oat2msltemp(self, oat, alt):
        # Convert layer temperature to mean sea level
        return oat + 0.0065 * alt - 273.15
    @classmethod
    def interpolate(self, t1, t2, alt1, alt2, alt):
        if (alt2 - alt1) == 0:
            print '[XPGFS] BUG: please report: ', t1, t2, alt1, alt2, alt
            return t2
        return t1 + (alt - alt1)*(t2 -t1)/(alt2 -alt1)
    @classmethod
    def fog2(self, rh):
        return (80 - rh)/20*24634
    @classmethod
    def toFloat(self, string, default = 0):
        # try to convert to float or return default
        try: 
            val = float(string)
        except ValueError:
            val = default
        return val
    @classmethod
    def rh2visibility(self, rh):
        # http://journals.ametsoc.org/doi/pdf/10.1175/2009JAMC1927.1
        return 1000*(-5.19*10**-10*rh**5.44+40.10) 
    @classmethod
    def dewpoint2rh(self, temp, dew):
        return 100*(exp((17.625*dew)/(243.04+dew))/exp((17.625*temp)/(243.04+temp)))
    @classmethod
    def dewpoint(self, temp, rh):
        return 243.04*(log(rh/100)+((17.625*temp)/(243.04+temp)))/(17.625-log(rh/100)-((17.625*temp)/(243.04+temp)))   
    @classmethod
    def shortHdg(self, a, b):
        if a == 360: a = 0
        if b == 360: b = 0
        if a > b:
            cw = (360 - a + b)
            ccw = -(a - b);
        else:
            cw = -(360 - b + a)
            ccw = (b - a)
        if abs(cw) < abs(ccw):
            return cw
        return ccw
    @classmethod
    def pa2inhg(self, pa):
        return pa * 0.0002952998016471232
    @classmethod
    def timeTrasition(self, current, new, elapsed, vel=0.5):
        '''
        Time based wind speed transition
        '''
        if current > new:
            dir = -1
        else:
            dir = 1
        if abs(current - new) < vel*elapsed + 0.1:
            return new
        else:
            return current + dir * vel * elapsed
    
    @classmethod
    def datarefTransition(self, dataref, new, elapsed,speed=0.25):
        '''
        Dataref time 
        '''
        # Save reference to ignore x-plane roundings
        id = str(dataref.DataRef)
        if not id in self.transrefs:
            self.transrefs[id] = dataref.value
        
        # Return if the value is already set
        if self.transrefs[id] == new:
            return
        
        current = self.transrefs[id]
        
        if current > new:
            dir = -1
        else:
            dir = 1
        if abs(current - new) > speed*elapsed + speed:
            new =  current + dir * speed * elapsed
        
        self.transrefs[id] = new
        dataref.value = new
    
    @classmethod
    def datarefTransitionHdg(self, dataref, new, elapsed, vel=1):
        '''
        Time based wind heading transition
        '''
        id = str(dataref.DataRef)
        if not id in self.transrefs:
            self.transrefs[id] = dataref.value
        
        if self.transrefs[id] == new:
            return
        
        current = self.transrefs[id]
        
        diff = c.shortHdg(current, new)
        if abs(diff) < vel*elapsed:
            newval = new
        else:
            if diff > 0:
                diff = +1
            else:
                diff = -1
            newval = current + diff * vel * elapsed
            if newval < 0:
                newval += 360
            else:
                newval %= 360
             
        self.transrefs[id] = newval
        dataref.value = newval
        
    
    @classmethod
    def limit(self, value, max = None, min = None):
        if max and value > max:
            return max
        elif min and value < min:
            return min
        else:
            return value
    @classmethod
    def cc2xp(self, cover):
        #Cloud cover to X-plane
        xp = cover/100.0*4
        if xp < 1 and cover > 0:
            xp = 1
        elif cover > 89:
            xp = 4
        return xp
    
    @classmethod
    def metar2xpprecipitation(self, type, int, mod):
        ''' Return intensity of a metar precipitation '''
        
        ints = {'-': 0, '': 1, '+': 2} 
        intensity = ints[int]
            
        types = {
         'DZ': [0.1, 0.2 , 0.3],
         'RA': [0.3 ,0.5, 0.8],
         'SN': [0.25 ,0.5, 0.8], # Snow
         'SH': [0.7, 0.8,  1]
         }
        
        if mod in ('SH', 'RE'):
            type = 'SH'
        
        if type in types:
            return types[type][intensity]