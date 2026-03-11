# Pi Launchpad Controller
 The Pi Lauchpad Controller is a full screen application that runs on a raspberry pi 3 with a 1920x480 display. Runs without desktop and should autostart. 
 Input is the Touchscreen
 It should have several Tabs on the left side for different panels.
 A mockup for the layout is presented here: 
![image](layout.png) 

Outputs should use the Midi Network protocoll used in 
```/lightcontrol/tools/NetworkMidi/Readme.md ```

## Tabs
For now we have 5 tabs (should be extendable)
Every tab should be a stand alone module.

### Preview
The first (top) Tab is the artnet preview window. A working display is under ```/lightcontrol/tools/PiDisplay/ ``` available and works fine. 

### Effect Tab
The effect tab should create a virtual "Novation Launch Control" as used in the Nachtgestalter3000.py, ```/lightcontrol/controllers/descriptions/launchcontrol.py ```.

### Color Tab
Should provide 2 color picker fields to the the primary and secondary synth color.
and expose it as a new network midi device

### Extras 
The effect tab should create a virtual "Novation Launch Control" as used in the Nachtgestalter3000.py, ```/lightcontrol/controllers/descriptions/launchcontrol.py ```.
2 Rows with 8 Knobs and 8 Buttons 

### Settings
Settings for the midi devices and for the preview window IP address, Preview Matrix resulution etc.

## Hardware 
- raspberry Pi3 
- Waveshare 8.8inch DSI Capacitive Touch Display, 480 × 1920, IPS, DSI Interface, 10-Point Touch. https://www.waveshare.com/8.8inch-dsi-lcd.htm 


## Existing Software 
- Network Midi Device: ```/lightcontrol/tools/NetworkMidi/ ```
- Artnet Preview: ```/lightcontrol/tools/PiDisplay/ ```

## To Do

