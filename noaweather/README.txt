===========================================
[XPGFS] Xplane NOAA Global Forecast weather
===========================================

Downloads METAR and Forecast data from NOAA servers and sets x-plane weather
using forecasted and reported data for the current time and world coordinates.

============
Requirements
============

Sandy Barbour Python Interface:
http://www.xpluginsdk.org/python_interface_latest_downloads.htm
Python: 2.7 http://www.python.org/getit/
Wgrib2: the plugin comes with wgrib2 for common os like osx, win32 and
linux i686 glib2.5. Wgrib uses cygwin on windows, the .dll is provided on the
bin folder and there's no need to install-it.

============
Installation
============

Install X-Plane Python interface:
http://www.xpluginsdk.org/python_interface_latest_downloads.htm

Copy the zip file contents to your X-Plane/Resources/plugins/PythonScripts folder.
The resulting installation should look like:

    X-Plane/Resources/plugins/PythonScripts/noaweather/
    X-Plane/Resources/plugins/PythonScripts/PI_noaaWeather.py

=========
RESOURCES
=========

NOOA:
-----
GFS Products:     http://www.nco.ncep.noaa.gov/pmb/products/gfs/
GFS Inventory:    http://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2f06.shtml
WAFS Inventory:   http://www.nco.ncep.noaa.gov/pmb/products/gfs/WAFS_blended_2012010606f06.grib2.shtml

NOMADS filter: http://nomads.ncep.noaa.gov/
wgrib2:        http://www.cpc.ncep.noaa.gov/products/wesley/wgrib2/

OpenGrADS:
----------
Interactive desktop tool for easy access, manipulation, and visualization of
earth science data and wgrib2 builds for diverse platforms.
url:           http://sourceforge.net/projects/opengrads/


XPlane:
-------
datarefs:      http://www.xsquawkbox.net/xpsdk/docs/DataRefs.html

Some info on what x-plane does with metar data:
               http://code.google.com/p/fjccuniversalfmc/wiki/Winds
