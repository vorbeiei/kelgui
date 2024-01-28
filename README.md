# KELgui
This is a python 3 GUI application designed to provide a cross-platform tool to control a Korad KEL103 electronic load over a serial connection. It will probably also work with a KEL102 load but this is untested and some functions might not work a 100% as expected.
Works with Linux and Windows and should work with macOS but has not been tested under it.

The following 3rd party libraries are used:
 - [KELctl](https://github.com/vorbeiei/kelctl) - Library specifically made with this app in mind to enable communication with the electronic load.
 - [PySide6](https://pypi.org/project/PySide6/) - Library to provide access to the Qt 6 framework to provide a GUI.
 - [PyQtGraph](https://github.com/pyqtgraph/pyqtgraph) - Library to provide graphics in python.
 - [Pglive](https://github.com/domarm-comat/pglive) - Library to provide easy live plotting using PyQtGraph.
 - [numpy](https://github.com/numpy/numpy) - fundamental package for scientific computing(used for graphs, data export).
 - [aenum](https://github.com/ethanfurman/aenum) - advanced enumerations

The minimum required python version is 3.10.

## Installation

### Packaged binary
At each release a single binary which includes all dependencies is created for Linux/Windows/macOS.
While this results in a fairly large file it should be as simple as just executing the file. (Apart from macOS probably, which is also untested)

### From source
The other option is to simply clone the repo and start main.py(```python main.py```).
In this case python 3.10 as a minimum version as well as all dependencies are needed.(see list of 3rd party libraries above)

## Hardware
The [ Korad KEL103/KEL102 ](https://www.koradtechnology.com/product/81.html) is sold under different names and brands i.e. RND 320-KEL103.


# Usage
Be aware that while in general the app should work pretty well, there are definitely several bugs and issues with it. Some of those are simply limitations of the load itself or problems with the communication protocol. For more details on (some) of those bugs refer to my own version of the [KEL 103 command documentation](https://github.com/vorbeiei/kelctl/blob/main/KEL103-protocol.md). 

## Settings tab
Under the settings tab there are several settings for both the device and the app itself.

### Limit values
These limits are device settings that are stored on the device and can be changed and retrieved here. These will be updated on connection to the load but not updated if changed outside of this app. In this case they can be updated with the "Get Limits" button. Setting them is done individually for each limit. The "Reset Limit" button will reset the limits to the maximum device limits for the KEL103. If using the app for a KEL102  which has different limits(at least for the power value) this might cause an error on the device.

### Device Settings
These settings are stored on the device and can be changed and retrieved here. These will NOT be updated on connection to the load and always have to be retrieved manually using the "GET" button. When clicking the "SET" button all settings will be set as shown therefore it would usually be best to first "GET" all current settings, make changes and then "SET" them.
The factory reset will simply trigger the built-in factory reset function. 
The "Init Saves" function will populate every save-slot for the battery, list, overcurrent protection test and overpower protection test modes with some predefined values. This is to prevent errors on load when trying to recall/retrieve an unused save slot for those modes. Be aware that this will OVERWRITE all currently saved values in those save-slots.

### Program Settings
Settings for program behaviour stored in a local config file.

#### Stop load on close
Will cause the load to stop the input when closing the application, otherwise the load will continue running.

#### Stop load on disconnect
Will cause the load to stop the input when disconnecting from it, otherwise the load will continue running.

#### Serial Debug Mode
Will cause serial debug message to be written to the console(when running from command line) which will show all sent and received messages over the serial connection.
Only will take effect after a disconnect/reconnect.

#### Enable crosshair
Will disable or enable the crosshair shown when hovering cursor over the graph in the control tab.

#### Graph timeframe
Will set the timeframe in seconds shown on the graph.

#### Measure interval
Will set the interval in seconds between each measurement taken from load. Will affect how often the graph is updated as well as values written to the data-log.

#### Serial - Baudrate
Will set the Baudrate used by the app to connect to the load. Will only be effective at the next connection.

## Control tab
### Main Controls Section
Contains the basic controls for the device.
A dropdown for selecting the serial port. A refresh button to update the list of available serial ports. A connect button for connecting/disconnecting to the load, a Start/Stop button to starting/stopping the input on the load. A trigger button to send the trigger signal to the device(Mainly used with the Pulse and Toggle mode).

### Output Section
#### Graph
Graph showing Voltage/Current/Power. Optional crosshair can be disabled in settings.
Graph controls include:
 - zooming using the scroll whell or holding the right mouse button and moving up/down or left/right.
 - Panning holding left mouse button
 - Clicking "A" button in lower left corner to reset view
 - Checkboxes below graph controlling which lines are displayed

#### Data Export
Used for exporting data-logs in CSV format.
"Export Data" button will export data selected in drowdown to the location selected in dialog.
"Clear" button will manually clear data in logs and graph-data.
Data is automatically cleared each time the "Start" button is pressed(but not when the same button is used as a "Stop" button).

#### Value display
Voltage/Current/Power/Mode are read from device at the measure interval set.
The runtime and charge value are only read from the device when in battery mode, otherwise those values are calculations based on measured current and runtime. Due to that there will be some inaccuracies with that value when not in battery mode.
The Energy value is only a calculation based on measured power and runtime.

### Mode selection/setting section
All available modes from the load are implemented here. Specific Notes for some of these notes below.

#### Basic Modes
Section for setting CC, CV, CR, CW and short modes as well as setting and recalling memory slots.
Memory slots are stored on the device and values can not be retrieved so recalling a slot will not show anything in this app but will change modes and set values on device based on saved values. Set values from basic modes can be set to slots and afterwards recalled.

#### List Mode
Setting and recalling List Mode. Validate button will check for errors in values without attempting to set them to device.
Repetitions are limited to a minimum of 3 due to a bug in the device that will not return anything when trying to recall a list with less than 3 repetitions.
"Clear all" button will remove values from all cells in table. "Clear marked" will remove values from marked cells in table.
All cells in a row have to have valid values in them or need to be empty(not just zero) for successfull validation.


# Making Changes
When making changes to the UI, the .ui file needs to be converted with uic for Qt6 i.e. ```uic6 mainwindow.ui > ui_mainwindow.py```
