# dwc2-for-klipper
A translator between DWC2 and Klipper

![Alt text](screenshots/screen_1.PNG?raw=true "screen 1")
![Alt text](screenshots/screen_2.PNG?raw=true "screen 2")

## What works

* printing from klippers virtual sdcard
* pause / cancel prints (resume?)
* babystepping feature using klippers ```SET_GCODE_OFFSET Z_ADJUST```
* editing klippers configuration. Its displayed as config.g in system section. So the webif restarts klipper after saving.
* Klipper macros are imported as virtual files and can be accesed from the dashboard
* uploads and downloads of gcodes
* gcode analysis using regex to determine duration / layerhighth / firstlayer / filamentusage and other
* the math for printtime left based on whatever, showing layerhistory, detecting layerchanges etc. (needs working slicer regex)
* settings of webinterface are saved and loded correctly
* homing / extruding / moving
* heater control
* setting fanspeed / extrusionmultipler / speedfactor during print in statuswindow
* Heightmap is working. (needs manual ```BED_MESH_CALIBATE```) It is kept only for displaying, even after ```BED_MESH_CLEAR```
* webcam integration works now
  * with mjpeg-streamer add the streaming url to settings. example: http://192.168.2.20:8080/?action=stream
  * With youtube live https://webcam.io/support/howto-embed-youtube-live/
* pause/resume/cancel macros are working now - see Things you should know
* plugin for Cura 4.0 does work, just enter url for example: http://192.168.2.188:4750/

## What is not working

* ~~webcam integration~~
* ~~heightmap~~
* printsimulation
* actual machinespeed, only displaying the requested values
  * klipper does not have this feedback (yet)? due to its lookahead feature 
  * can we calc this? movelength/junction/acceleration is there
* ~~cancel/pause/resume macros. I will do this soon~~
* ~~rrf/dwc cura plugin~~
* ......

## Things you should know

* Klipper messages are marked as warnings.(yellow)
  * Noramly klipper knows ok and error
* Klippers printer.cfg is displayed as a virtual file (config.g) in System section
  * restart after conf edits works
* The macros you define in printer.cfg are displayed as virtual files wthin DWCs macros
* For pause and resume macros you can use:
  * kliper gcode macros pause_print resume_print cancel_print (not case sensitive)
  * dwc macros pause.g resume.g cacnel.g - this is in line with rrf
  * dwc macros are overriding klippers
* ....

## Installation

### Prerequirements
python2, tornado, gunzip, unzip, wget

##### On arch:
```
sudo pacman -Sy && pacman -S python2 python2-tornado wget gunzip
```

Maybe you´ll need to change the startup system for klipper to access ~/klipper/klippy/klippy.py

##### On Octopi / Ubuntu / Debian (untested, feedback wanted)
I asume here that you used the octopi install script from Kevins github.
```
sudo apt install wget gzip tar
```

Then switch to your klipper user and:
```
sudo systemctl stop klipper
cd ~
mv klipper klipper_backup 
PYTHONDIR="${HOME}/klippy-env"
virtualenv ${PYTHONDIR}
${PYTHONDIR}/bin/pip install tornado==5.1.1

git clone https://github.com/KevinOConnor/klipper.git
git clone https://github.com/Stephan3/dwc2-for-klipper.git
ln -s ~/dwc2-for-klipper/web_dwc2.py ~/klipper/klippy/extras/web_dwc2.py
# make changes in klipper we need
gcode=$(sed 's/self.bytes_read = 0/self.bytes_read = 0\n        self.respond_callbacks = []/g' klipper/klippy/gcode.py)
gcode=$(echo "$gcode" | sed 's/# Response handling/def register_respond_callback(self, callback):\n        self.respond_callbacks.append(callback)/')
gcode=$(echo "$gcode" | sed 's/os.write(self.fd, msg+"\\n")/os.write(self.fd, msg+"\\n")\n            for callback in self.respond_callbacks:\n                callback(msg+"\\n")/')
echo "$gcode" > klipper/klippy/gcode.py

mkdir -p ~/sdcard/dwc2/web
mkdir -p ~/sdcard/sys
cd ~/sdcard/dwc2/web 
wget https://github.com/chrishamm/DuetWebControl/releases/download/2.0.6/DuetWebControl-SBC.zip
unzip *.zip && for f_ in $(find . | grep '.gz');do gunzip ${f_};done
sudo systemctl start klipper
```

##### if you want backwards compatibility to dwc 1:
```
cd ~/sdcard/dwc2/web 
wget https://github.com/chrishamm/DuetWebControl/releases/download/1.22.5/DuetWebControl-1.22.5.zip
unzip DuetWebContro*.zip
for f_ in $(find . | grep '.gz');do gunzip ${f_};done
```

### Klipper config example:
```
[virtual_sdcard]
path: /home/pi/sdcard

[web_dwc2]
# optional - defaulting to Klipper
printer_name: Reiner Calmund
# optional - defaulting to 127.0.0.1
listen_adress: 0.0.0.0
# needed - use above 1024 as nonroot
listen_port: 4750
#	optional defaulting to dwc2/web. Its a folder relative to your virtual sdcard.
web_path: dwc2/web
```

## Fix missing stuff in klipper today
A Gcode callback and ack system is missing in klippy today for other objects than the serial. You need to use my klipper fork or patch the few lines by hand in gcode.py.
See https://github.com/KevinOConnor/klipper/pull/1290
