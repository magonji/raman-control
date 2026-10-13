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
2. The spectrum is read from a track of CCD rows (`read_mode = "random_track"`), rows 67
   to 72 by default (`tracks = [[67, 72]]`), numbered as in Solis: from 1, both ends
   included. Without a pinhole, these rows are the spatial filter: a narrow track on the
   image of the spot leaves out light from outside the focal plane (more confocal, less
   background, fewer counts); a wide one collects more of everything. To choose them,
   **CCD image…** in the acquisition group reads the whole CCD and shows it with the
   vertical profile of a band (yellow); drag the rows (green) over either, or press
   **Suggest** (rows above half the profile's maximum) or **Default** (the rows in
   `config.toml`), then **Apply**. Typical: laser on silicon, focus, band at 520 cm⁻¹.
   The rows applied are remembered between sessions, saved with every spectrum
   (`tracks_rows`) and a background taken with other rows is not subtracted. They can
   also be typed in the acquisition group (**Apply rows**, **Default**). Alternatively,
   `read_mode = "multi_track"` with `mt_number`, `mt_height` and `mt_offset` copied from
   Solis, or `read_mode = "fvb"` for full vertical binning.
3. Check in Solis which turret index each grating is and adjust
   `[spectrometer.grating_labels]` (the list in the file replaces the default one, so
   list only the gratings the turret has).
4. **Spectral range.** The centre of the window can be typed in nm or in cm⁻¹; before
   **Move**, the panel shows the range it would cover. **Window** presets set grating
   and centre in one go: those of `[[spectrometer.window_presets]]` in `config.toml`
   plus any saved with **Save as preset…** (kept on this computer). For a range wider
   than one window, **Step and glue** (from–to, in cm⁻¹) measures the windows needed,
   overlapping by `glue_overlap` (10 %), with the measurement's exposure and
   accumulations (no auto-exposure, no background subtraction), fades each seam into
   the next and goes back to the window it started from. The log gives the intensity
   ratio at each seam (1 when the windows agree; far from it points to vignetting or
   a CCD not yet stable). These controls are also in the third row of the image window.
5. Set `simulate = false` and connect. The camera starts cooling to −65 °C; the
   **Acquire** button does nothing until the status is "Stable".
6. If you get a DLL error, set `dll_dir` to the folder containing `atmcd64d.dll` and
   `ShamrockCIF64.dll` (usually the Andor SOLIS one).
7. Compare a spectrum with one from Solis under the same conditions (same grating,
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
Closing either window closes the program, and only once every instrument is
disconnected (see step 10 below). The laser stop is on both, and F12 works in either.

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
7. The **Spectra** plot shows the current spectrum over every one saved this session.
   The list under it holds them all, each with its colour: untick one to hide it,
   select some and **Remove selected** to take them off (**Clear** takes them all).
   There are six colours; each spectrum keeps its own, those removed free theirs for
   the next ones, and with all six in use newcomers are grey. **Saved: Normal · Dim ·
   Hide** fades them all behind the current one or hides them (hidden ones are left out
   of the zoom). The continuous measurement is the live
   view: its spectra are the current one and are not saved (keep one with **Save latest
   continuous spectrum**). **Acquire** takes a measurement, always saved, which joins
   the saved ones. Acquire also works during the continuous measurement: once the
   continuous exposure in progress ends, the measurement runs, is saved, and the
   continuous measurement resumes by itself. Esc or **Stop** stop everything, without resuming. In
   the image window, the two buttons after the camera ones are the continuous
   measurement and Acquire. The continuous measurement has its own exposure and accumulations (defaults in
   `continuous_exposure_s` and `continuous_accumulations`) and never uses
   auto-exposure: in the image window its handle is the orange diamond (◆) on the
   exposure and accumulation sliders, next to the round one (●) for measurements; in
   the control window, the **Continuous** column of the acquisition boxes.
8. Three buttons after the measurement ones set the zoom of the Spectra plot, and keep
   it as new spectra arrive until it is zoomed by hand with the mouse: **full view** (every spectrum whole),
   **rescale** (the intensity spans what is visible in the current wavelength window)
   and **rescale without Rayleigh** (the same, leaving out |shift| below
   `rayleigh_exclude_cm1`, 10 cm⁻¹). As in Andor Solis, the small buttons by the foot
   of the intensity axis and under the wavelength axis zoom that axis in or out
   (intensity keeping the baseline, wavelength about the centre); dragging an axis
   moves along it, and the mouse wheel over an axis zooms only that one.
9. Disconnecting the CCD (its own button or the power button in the image window)
   always warms it above 0 °C first (Andor asks for at least −20 °C). Changed your mind?
   **Keep cooling (cancel warm-up)** (the warm-up button meanwhile), Esc or **Stop**
   cancel it: the CCD stays connected and the cooler goes back on at once, cooling from
   the temperature it had reached instead of after warming up completely.
10. Closing the program (either window) first disconnects everything in order: emission
   off and confirmed, camera, CCD warmed up if cold. The windows stay open meanwhile and
   close by themselves at the end. If something cannot be disconnected (the laser does
   not confirm it is off, the warm-up is interrupted), closing is cancelled and the log
   says why. Closing again while it waits offers to quit without waiting.

Auto-exposure avoids saturating any pixel, including the residual laser line. If that
line is the strongest thing in the spectrum, the exposure will be limited by it: this
is the prudent choice, because saturating the CCD near the laser can bleed into
neighbouring pixels.

## Saved data

Everything goes into a folder per day inside the data folder, named after the sample
(spaces become `_`) with a consecutive number per sample and day. The sample name can
be typed in the control window or on the title line of the current spectrum. Every
file carries its number, which carries on from the files already in the folder, so it
survives restarting the program. A spectrum saved with the camera image
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
