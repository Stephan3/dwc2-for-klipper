# DO NOT USE THIS ANYMORE!
# This is only here for refference. please use:
[Socket version](https://github.com/Stephan3/dwc2-for-klipper-socket)


--------------------------------------------------

# dwc2-for-klipper

A translator between [DWC2](https://github.com/Duet3D/DuetWebControl) and Klipper

![Alt text](screenshots/screen_1.PNG?raw=true "screen 1")
![Alt text](screenshots/screen_2.PNG?raw=true "screen 2")

## What works

* printing from Klipper's virtual sdcard
* pause / cancel prints (resume?)
* babystepping feature using Klipper's ```SET_GCODE_OFFSET Z_ADJUST```
* editing Klipper's configuration. Its displayed as `config.g` in system section. So the web interface restarts Klipper after saving.
* Klipper macros are imported as virtual files and can be accesed from the dashboard
* uploads and downloads of gcodes
* gcode analysis using regex to determine duration / layerhight / firstlayer / filamentusage and others
* the math for printtime left based on whatever, showing layerhistory, detecting layerchanges etc. (needs working slicer regex)
* settings of web interface are saved and loaded correctly
* homing / extruding / moving
* heater control
* setting fanspeed / extrusionmultiplier / speedfactor during print in status window
* heightmap is working (needs manual `BED_MESH_CALIBATE`). It is kept only for displaying, even after `BED_MESH_CLEAR`
* webcam integration works now
  * with mjpeg-streamer add the streaming URL to settings; example: http://192.168.2.20:8080/?action=stream
  * With YouTube Live; example: https://webcam.io/support/howto-embed-youtube-live/
* pause/resume/cancel macros are working now - see Things You Should Know below
* plugin for Cura 4.0 does work, just enter URL; for example: http://192.168.2.188:4750/

## What is not working

* ~~webcam integration~~
* ~~heightmap~~
* printsimulation
* actual machinespeed, only displaying the requested values
  * klipper does not have this feedback (yet)? due to its lookahead feature 
  * can we calc this? movelength/junction/acceleration is there
* ~~cancel/pause/resume macros. I will do this soon~~
* ~~rrf/dwc cura plugin~~
* ...

## Things you should know

* Klipper messages are marked as warnings (yellow).
  * Normaly Klipper knows ok and error
* Klipper's `printer.cfg` is displayed as a virtual file (`config.g`) in system section
  * restart after configuration edits works
* The macros you define in `printer.cfg` are displayed as virtual files wthin DWC's macros
* For pause and resume macros you can use:
  * Klipper gcode macros `pause_print`, `resume_print`, `cancel_print` (not case sensitive)
  * DWC macros `pause.g`, `resume.g`, `cancel.g` - this is in line with RRF
  * DWC macros are overriding Klipper's macros
* ...

## Installation

### Prerequisites

Python 2, Tornado, gunzip, unzip and wget.

#### On ArchLinux:

```
sudo pacman -Syu python2 python2-tornado wget gzip
```

Maybe you´ll need to change the startup system for Klipper to access `~/klipper/klippy/klippy.py`.

#### On Debian-based systems such as Octopi or Ubuntu:

I asume here that you used the Octopi install script from Kevin's GitHub repository.

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

mkdir -p ~/sdcard/dwc2/web
mkdir -p ~/sdcard/sys
cd ~/sdcard/dwc2/web 
wget https://github.com/Duet3D/DuetWebControl/releases/download/3.1.1/DuetWebControl-SD.zip
unzip *.zip && for f_ in $(find . | grep '.gz');do gunzip ${f_};done
sudo systemctl start klipper
```

#### If you want backwards compatibility to DWC1:

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
