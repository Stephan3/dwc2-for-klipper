# dwc2-for-klipper
A translator between DWC2 and Klipper

# Installation

For testing its sitting on root user, this will change once this here will be public.

```
git clone https://github.com/Stephan3/klipper.git
git clone https://{your_git_user_here}@github.com/Stephan3/dwc2-for-klipper.git
ln -s dwc2-for-klipper/web_dwc2.py klipper/klippy/extras/web_dwc2.py
mkdir -p /root/sdcard/dwc2/web 
cd /root/sdcard/dwc2/web 
wget -q  https://github.com/chrishamm/DuetWebControl/releases/download/2.0.0-RC3/DuetWebControl.zip
unzip *.zip && for f_ in $(find . | grep '.gz');do gunzip ${f_};done
```

Klipper config example:
```
[virtual_sdcard]
path: /root/sdcard

[web_dwc2]
listen_adress: 0.0.0.0
listen_port: 4750
#	folder on sdcard
web_path: dwc2/web
```

# Fix missing stuff in klipper today
A Gcode feedback is missing in klippy to work. You need to use my klipper fork or patch the few lines by hand.
See https://github.com/KevinOConnor/klipper/pull/1203

Könnte mir btw mal einer erklären, warum ich das in englisch schreibe ? :D
Das lesen doch eh nur Deutschsprachige.
