# Raman microscope control panel

A single Python application for the 532 nm Raman laser (Laser Quantum), the Andor CCD
with the Shamrock 500i spectrograph, and the Teledyne DALSA Genie Nano microscope
camera. For day-to-day work it replaces the RemoteApp Laser Control, Andor Solis and
Sapera CamExpert.

What it adds compared with using the three programs separately:

- Auto-exposure: finds the time that brings the highest peak to 70 % of saturation.
- Cosmic-ray removal by comparing accumulations (or, with only one, by their shape).
- Background subtraction, only if the background was taken under the same conditions.
- Live cm⁻¹ axis and calibration of 0 cm⁻¹ using the residual laser line.
- Interlock: will not acquire until the CCD is stable at −65 °C.
- Every spectrum is saved with a JSON metadata file (power, temperatures, grating,
  exposure…) and, optionally, with the microscope image at that moment.
- Laser stop always visible (F12) and an orderly shutdown that warms up the CCD.

Everything starts in **simulation**: you can practise the whole workflow without
touching the equipment.

---

## Installation (Windows)

1. Install **64-bit Python 3.11** from python.org (tick "Add python.exe to PATH").
   3.11 is recommended because it is the version best supported by `harvesters`/`genicam`.
2. Copy this folder to the Raman PC, for example to `C:\RamanControl`.
3. Open a terminal (cmd) in that folder and run:

   ```bat
   py -3.11 -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Check that everything is fine with the tests (they need no hardware):

   ```bat
   .venv\Scripts\python.exe -m pip install pytest
   .venv\Scripts\python.exe -m pytest tests
   ```

## First start: simulation

Double-click `start_simulation.bat`. Press **Connect all**, set a power, switch
emission on and wait for the simulated CCD to reach −65 °C and stabilise. Then press
**Acquire**. The simulated laser really illuminates the simulated sample: with no
emission you will only see noise, and as the power goes up the bands appear (including
the low-frequency ones and the residual Rayleigh light next to 0 cm⁻¹).

---

## Bringing up the real hardware, one instrument at a time

Create `config.local.toml` next to `config.toml` and set `simulate = false` there, on
**one instrument only**; test it and move on to the next. For example:

```toml
[laser]
simulate = false
```

`config.local.toml` is applied on top of `config.toml` and git ignores it, so each
computer keeps its own and `git pull` never puts the Raman PC back into simulation.
Use `start_panel.bat` to start with the configuration.

> Each instrument accepts only one program at a time. Before connecting, close the
> RemoteApp Laser Control, Andor Solis and Sapera CamExpert as appropriate.

### 1. Laser (Laser Quantum, RS-232)

1. In Device Manager → *Ports (COM & LPT)*, note the controller's port and put it in
   `[laser] port`.
2. The serial settings and the commands in `[laser.commands]` follow the *gem with
   smd12* manual: 9600 baud, 8N1, no handshaking, commands ending in CR. If it does
   not connect, run `.venv\Scripts\python.exe tools\probe_laser.py COM4`: it tries
   the usual settings with read-only queries only and tells you which ones the
   controller answers to. `POWER?` should return something like `0.6mW`.
3. Set `simulate = false`, start the program and press **Connect**. If the status shows
   "Status not recognised", adjust `get_status` or the reply your firmware returns.
4. Power is limited to **500 mW in the driver itself**; even if you edit the file with
   a higher value, the program reduces it to 500.

### 2. Andor CCD + Shamrock

1. Leave Andor Solis installed (its DLLs are the ones `pylablib` uses), but **close it**.
2. By default the spectrum is read from a single track, rows 67 to 72 of the CCD
   (`read_mode = "random_track"`, `tracks = [[67, 72]]`), numbered as in Solis: from 1,
   both ends included. The best rows depend on where the signal falls on the CCD, so
   adjust them during the measurements and keep them in `config.local.toml`; several
   tracks (`[[60, 65], [67, 72]]`) are added together. Alternatively, `read_mode =
   "multi_track"` with `mt_number`, `mt_height` and `mt_offset` copied from Solis
   (*Acquisition Setup → MT Setup*), or `read_mode = "fvb"` for full vertical binning.
3. Check in Solis which turret index each grating is and adjust
   `[spectrometer.grating_labels]`.
4. Set `simulate = false` and connect. The camera starts cooling to −65 °C; the
   **Acquire** button does nothing until the status is "Stable".
5. If you get a DLL error, set `dll_dir` to the folder containing `atmcd64d.dll` and
   `ShamrockCIF64.dll` (usually the Andor SOLIS one).
6. Compare a spectrum with one from Solis under the same conditions (same grating,
   centre 574.0 nm, exposure): the nm axis should match.

### 3. Microscope camera (Genie Nano, GigE Vision)

1. `harvesters` needs a *GenTL producer* (`.cti` file), and Sapera does not install a
   usable one. Install Teledyne's **Spinnaker SDK** and use the **64-bit** `.cti` that
   comes with it (Python is 64-bit, so the 32-bit one will not load). To find it, search
   the whole drive (only a handful of `.cti` files turn up):

   ```bat
   dir /s /b C:\*.cti
   ```

   Pick Spinnaker's one in a `cti64` folder (or with `64` in its name) and put its full path in
   `[camera] cti_path` in `config.local.toml`. The lines about `DSAnnounceCompositeBuffer`,
   `DSGetNumFlows` and similar that appear in the console when connecting are harmless:
   the producer lacks some optional GenTL functions that the program does not use.
2. **Network and firewall.** The camera communicates over UDP, and Windows Firewall may
   block `python.exe` even though CamExpert works. In an administrator terminal:

   ```bat
   netsh advfirewall firewall add rule name="Raman panel" dir=in action=allow program="C:\RamanControl\.venv\Scripts\python.exe" enable=yes
   ```

   Repeat the rule with the path of the base Python (`where python`), because the
   virtual environment's `python.exe` launches the system one. Also mark the camera's
   network as *Private* in Windows settings.
3. To stop the video dropping packets: a dedicated network card, *Jumbo frames* at
   9000 in its advanced properties and `packet_size = 8192` in `config.toml`. If the
   Teledyne GigE Vision filter driver is installed, leave it bound to that card.
4. Set `simulate = false`, close CamExpert and connect.

---

## Daily use

The program opens two windows: the **control** window (instruments, saving and log) and
the **image and spectrum** window. With two monitors, the image and spectrum window opens
maximised on the main screen and the control window on the other monitor; after that
each one remembers where you left it. Devices that Windows counts as screens but are not
monitors, such as the BNS spatial light modulator, are never used: they are listed in
`ignore_screens` under `[windows]` in `config.toml`.
Closing the image and spectrum window only hides it (bring it back with **Show image and
spectrum**); the program is closed from the control window. The laser stop is on both,
and F12 works in either.

1. **Connect all.** The CCD starts cooling (a few minutes).
2. Start the video, focus on the sample and drag the green marker to the laser spot.
3. Set the power, **Apply**, **Emission on** (asks you to confirm goggles the first
   time). The green strip shows that the laser is emitting.
4. With the CCD stable: choose exposure and accumulations (3 or more to remove cosmic
   rays properly) or tick **Auto-exposure**, and **Acquire**. **Continuous** repeats
   until you press **Stop** (or Esc).
5. **Background:** with the beam blocked and the same conditions, **Acquire
   background**; then tick **Subtract background**. If you change exposure, grating or
   centre, it has to be repeated.
6. **Calibrate 0 cm⁻¹:** with a scattering sample, the button looks for the Rayleigh
   light let through by the notch filters and corrects the laser wavelength. Note the
   value in `wavelength_nm` if you want to keep it.
7. Every spectrum is saved automatically (**Save every spectrum automatically** is ticked
   by default), except those of a continuous measurement, which is a live preview: keep
   one of those with **Save latest spectrum**. In the image window, the two buttons
   after the camera ones start/stop the continuous measurement and take one spectrum.
8. Disconnecting the CCD (its own button or the power button in the image window)
   always warms it above 0 °C first (Andor asks for at least −20 °C); Esc or **Stop** interrupts
   the warm-up and leaves it connected. On exit, the program switches emission off and
   offers to warm the CCD up or to quit without warming it.

Auto-exposure avoids saturating any pixel, including the residual laser line. If that
line is the strongest thing in the spectrum, the exposure will be limited by it: this
is the prudent choice, because saturating the CCD near the laser can bleed into
neighbouring pixels.

## Saved data

Everything goes into a folder per day inside the data folder, named after the sample
(spaces become `_`) with a consecutive number per sample and day. The sample name can
be typed in the control window or above the current spectrum, which also shows the
name the next file will get. The numbering carries on from the files already in the
folder, so it survives restarting the program. A spectrum saved with the camera image
shares its number; the date and time of each measurement are in its JSON.

```
C:/Datos_Raman/20261007/
  quartz_crystal_001_spectrum.csv    wavelength_nm, raman_shift_cm-1, counts[, background]
  quartz_crystal_001_spectrum.json   all the measurement parameters
  quartz_crystal_001_image.tif       microscope image (8/12/16-bit, lossless)
  quartz_crystal_001_image.json      exposure, gain, laser marker position
  quartz_crystal_002_spectrum.csv    ...
```

The CSV repeats the metadata as `#` comments, so it can be read directly with
`numpy.loadtxt(..., delimiter=",")` or `pandas.read_csv(..., comment="#")`.

## Safety: what the program does and does not do

It does: limit the power to 500 mW in the driver, ask for confirmation before the first
emission, always show whether the laser is emitting, switch emission off with F12 or on
disconnect/exit (the laser is only disconnected once the measured power confirms it is
off), and refuse to acquire with an unstabilised CCD.

It does not: replace the interlock, the controller key, the goggles or the laboratory
rules. **Software must never be the only safety barrier.** The 1064 nm and 1040 nm
lasers and the SLM are not controlled from this program.

---

## Code structure

```
main.py                        start-up (--sim, --config)
config.toml                    all the configuration
raman_control/
  hardware/                    one real driver and one simulator per instrument
    laser.py                   Laser Quantum over RS-232 (pyserial)
    spectrometer.py            Andor SDK2 + Shamrock (pylablib)
    camera.py                  Genie Nano (harvesters / GenICam)
  workers.py                   one thread per instrument, with a command queue
  acquisition.py               cm⁻¹, cosmic rays, auto-exposure, laser line
  storage.py                   CSV/JSON/TIFF
  gui/                         panels, control window (main_window.py) and image
                               and spectrum window (view_window.py), PySide6 + pyqtgraph
tests/                         hardware-free tests
```

The main rule: **the interface never talks to the hardware**. It sends commands with
`worker.submit("command", ...)` and receives results through signals. To add a new
function to an instrument: a method in its driver (and in the simulator), a `cmd_...`
in its worker and a button that calls `submit`. The natural next steps are a motorised
stage for Raman maps and control of the infrared lasers.

## Troubleshooting

| Symptom | What to check |
|---|---|
| "Cannot open COM3" | The RemoteApp or another program has the port open; wrong port. |
| The laser connects but the status is "not recognised" | The `get_status` command or its reply differs in your firmware: check it in Tera Term. |
| Error opening the Andor | Andor Solis is still open; DLLs not found (`dll_dir`); 32-bit Python. |
| Axis in pixels instead of nm | The Shamrock did not open (see the log); check the cable or the spectrograph DLLs. |
| "No GigE camera detected" | CamExpert open, firewall, camera IP outside the subnet, `.cti` missing or the 32-bit one (use Spinnaker's 64-bit `.cti`). |
| The video is jerky | Jumbo frames, `packet_size`, dedicated network card, GigE filter driver. |
| It will not acquire | The CCD is not "Stable" yet. For testing, tick "Allow without stable CCD". |

Everything shown in the log is also saved to `raman_control.log`.
