# Panel de control del microscopio Raman

Una sola aplicación en Python para el láser Raman de 532 nm (Laser Quantum), el CCD
Andor con el espectrógrafo Shamrock 500i y la cámara del microscopio Teledyne DALSA
Genie Nano. Sustituye, para el trabajo diario, a la RemoteApp Laser Control, Andor
Solis y Sapera CamExpert.

Lo que añade respecto a usar los tres programas por separado:

- Autoexposición: busca el tiempo que lleva el pico más alto al 70 % de la saturación.
- Eliminación de rayos cósmicos comparando acumulaciones (o, con una sola, por su forma).
- Resta de fondo, solo si el fondo se tomó en las mismas condiciones.
- Eje en cm⁻¹ en directo y calibración del 0 cm⁻¹ con la línea láser residual.
- Bloqueo: no adquiere hasta que el CCD esté estable a −65 °C.
- Cada espectro se guarda con un JSON de metadatos (potencia, temperaturas, red,
  exposición…) y, si quieres, con la imagen del microscopio en ese instante.
- Paro del láser siempre visible (F12) y cierre ordenado que calienta el CCD.

Todo arranca en **simulación**: puedes practicar el flujo completo sin tocar el equipo.

---

## Instalación (Windows)

1. Instala **Python 3.11 de 64 bits** desde python.org (marca «Add python.exe to PATH»).
   Se recomienda 3.11 porque es la versión con mejor soporte de `harvesters`/`genicam`.
2. Copia esta carpeta al PC del Raman, por ejemplo en `C:\RamanControl`.
3. Abre una terminal (cmd) en esa carpeta y ejecuta:

   ```bat
   py -3.11 -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Comprueba que todo está bien con las pruebas (no necesitan hardware):

   ```bat
   .venv\Scripts\python.exe -m pip install pytest
   .venv\Scripts\python.exe -m pytest tests
   ```

## Primer arranque: simulación

Doble clic en `iniciar_simulacion.bat`. Pulsa **Conectar todo**, fija una potencia,
enciende la emisión y espera a que el CCD simulado llegue a −65 °C y se estabilice.
Luego pulsa **Adquirir**. El láser simulado ilumina de verdad la muestra simulada: sin
emisión solo verás ruido y, al subir la potencia, aparecen las bandas (también las de
baja frecuencia y el resto de luz Rayleigh junto a 0 cm⁻¹).

---

## Poner en marcha el hardware real, de uno en uno

Edita `config.toml`, cambia `simulate = false` en **un solo instrumento**, pruébalo y
pasa al siguiente. Usa `iniciar_panel.bat` para arrancar con la configuración.

> Cada instrumento solo admite un programa a la vez. Antes de conectar, cierra la
> RemoteApp Laser Control, Andor Solis y Sapera CamExpert según corresponda.

### 1. Láser (Laser Quantum, RS-232)

1. En el Administrador de dispositivos → *Puertos (COM y LPT)*, anota el puerto del
   controlador y ponlo en `[laser] port`.
2. **Verifica el juego de comandos** en el manual del controlador (SMD12/mpc). Los de
   `[laser.commands]` son los habituales en Laser Quantum, pero hay que confirmarlos.
   Puedes probarlos a mano en Tera Term (19200 baudios, 8N1, fin de línea CR+LF):
   `POWER?` debe devolver algo como `0.6mW` y `STATUS?` algo con `ENABLED`/`DISABLED`.
3. Pon `simulate = false`, arranca y pulsa **Conectar**. Si el estado aparece como
   «Estado no reconocido», ajusta `get_status` o la respuesta que devuelve tu firmware.
4. La potencia está limitada a **500 mW en el propio driver**; aunque edites el
   archivo con un valor mayor, el programa lo reduce a 500.

### 2. CCD Andor + Shamrock

1. Deja instalado Andor Solis (sus DLL son las que usa `pylablib`), pero **ciérralo**.
2. En Solis, abre *Acquisition Setup → MT Setup* y copia el número de pistas, la altura
   y el desplazamiento en `mt_number`, `mt_height` y `mt_offset`. Si prefieres binning
   vertical completo, usa `read_mode = "fvb"`.
3. Comprueba en Solis qué índice de la torreta es cada red y ajusta
   `[spectrometer.grating_labels]`.
4. Pon `simulate = false` y conecta. La cámara empieza a enfriar a −65 °C; el botón
   **Adquirir** no funciona hasta que el estado sea «Estable».
5. Si da error de DLL, indica en `dll_dir` la carpeta donde están `atmcd64d.dll` y
   `ShamrockCIF64.dll` (normalmente la de Andor SOLIS).
6. Compara un espectro con uno de Solis en las mismas condiciones (misma red, centro
   574,0 nm, exposición): el eje en nm debe coincidir.

### 3. Cámara del microscopio (Genie Nano, GigE Vision)

1. `harvesters` necesita un *GenTL producer* (archivo `.cti`). Búscalo:

   ```bat
   echo %GENICAM_GENTL64_PATH%
   dir /s /b "C:\Program Files\Teledyne DALSA\*.cti"
   ```

   Si aparece, déjalo en automático o pon su ruta en `[camera] cti_path`. Si Sapera no
   instala ninguno, sirve un producer GigE Vision genérico de otro fabricante (por
   ejemplo mvGenTL de MATRIX VISION), que funciona con cualquier cámara GigE Vision.
2. **Red y firewall.** La cámara se comunica por UDP, y el firewall de Windows puede
   bloquear a `python.exe` aunque CamExpert funcione. En una terminal de administrador:

   ```bat
   netsh advfirewall firewall add rule name="Raman panel" dir=in action=allow program="C:\RamanControl\.venv\Scripts\python.exe" enable=yes
   ```

   Repite la regla con la ruta del Python base (`where python`), porque el `python.exe`
   del entorno virtual lanza al del sistema. Marca además la red de la cámara como
   *Privada* en la configuración de Windows.
3. Para que el vídeo no pierda paquetes: tarjeta de red dedicada, *Jumbo frames* a
   9000 en sus propiedades avanzadas y `packet_size = 8192` en `config.toml`. Si el
   driver de filtro GigE Vision de Teledyne está instalado, déjalo enlazado a esa tarjeta.
4. Pon `simulate = false`, cierra CamExpert y conecta.

---

## Uso diario

1. **Conectar todo.** El CCD empieza a enfriar (unos minutos).
2. Iniciar vídeo, enfocar la muestra y arrastrar el marcador verde hasta el punto del láser.
3. Fijar la potencia, **Aplicar**, **Encender emisión** (pide confirmar las gafas la
   primera vez). La franja verde indica que el láser está emitiendo.
4. Con el CCD estable: elegir exposición y acumulaciones (3 o más para eliminar bien los
   rayos cósmicos) o marcar **Autoexposición**, y **Adquirir**. **Continuo** repite hasta
   pulsar **Detener** (o Esc).
5. **Fondo:** con el haz bloqueado y las mismas condiciones, **Adquirir fondo**; luego
   marca **Restar fondo**. Si cambias exposición, red o centro, hay que repetirlo.
6. **Calibrar 0 cm⁻¹:** con una muestra que disperse, el botón busca la luz Rayleigh que
   dejan pasar los notch y corrige la longitud de onda del láser. Anota el valor en
   `wavelength_nm` si quieres conservarlo.
7. Guardar a mano o marcar **Guardar cada espectro automáticamente**.
8. Al salir, el programa apaga la emisión y ofrece calentar el CCD por encima de −20 °C
   antes de desconectarlo, como recomienda Andor.

La autoexposición evita saturar cualquier píxel, incluido el resto de línea láser. Si
esa línea es lo más intenso del espectro, la exposición quedará limitada por ella: es
lo prudente, porque saturar el CCD cerca del láser puede desbordar a los píxeles vecinos.

## Datos que se guardan

```
20260925_143012_celula03_espectro.csv    wavelength_nm, raman_shift_cm-1, counts[, background]
20260925_143012_celula03_espectro.json   todos los parámetros de la medida
20260925_143012_celula03_imagen.tif      imagen del microscopio (12/16 bits, sin pérdidas)
20260925_143012_celula03_imagen.json     exposición, ganancia, posición del marcador del láser
```

El CSV repite los metadatos como comentarios `#`, así que se lee directamente con
`numpy.loadtxt(..., delimiter=",")` o `pandas.read_csv(..., comment="#")`.

## Seguridad: qué hace y qué no hace el programa

Hace: limitar la potencia a 500 mW en el driver, pedir confirmación antes de la primera
emisión, mostrar siempre si hay emisión, apagarla con F12 o al desconectar/salir, y no
adquirir con el CCD sin estabilizar.

No hace: sustituir al interlock, la llave del controlador, las gafas ni las normas del
laboratorio. **El software nunca debe ser la única barrera de seguridad.** Los láseres
de 1064 nm y 1040 nm y el SLM no se controlan desde este programa.

---

## Estructura del código

```
main.py                        arranque (--sim, --config)
config.toml                    toda la configuración
raman_control/
  hardware/                    un driver real y un simulador por instrumento
    laser.py                   Laser Quantum por RS-232 (pyserial)
    spectrometer.py            Andor SDK2 + Shamrock (pylablib)
    camera.py                  Genie Nano (harvesters / GenICam)
  workers.py                   un hilo por instrumento, con cola de órdenes
  acquisition.py               cm⁻¹, rayos cósmicos, autoexposición, línea láser
  storage.py                   CSV/JSON/TIFF
  gui/                         paneles y ventana principal (PySide6 + pyqtgraph)
tests/                         pruebas sin hardware
```

La regla principal: **la interfaz nunca habla con el hardware**. Envía órdenes con
`worker.submit("orden", ...)` y recibe resultados por señales. Para añadir una función
nueva a un instrumento: un método en su driver (y en el simulador), un `cmd_...` en su
worker y un botón que haga `submit`. Las ideas naturales para seguir son una platina
motorizada para mapas Raman y el control de los láseres de infrarrojo.

## Problemas frecuentes

| Síntoma | Qué revisar |
|---|---|
| «No se puede abrir COM3» | La RemoteApp u otro programa tiene el puerto abierto; puerto equivocado. |
| El láser conecta pero el estado es «no reconocido» | El comando `get_status` o su respuesta difieren en tu firmware: revísalo en Tera Term. |
| Error al abrir la Andor | Andor Solis sigue abierto; DLL no encontradas (`dll_dir`); Python de 32 bits. |
| Eje en píxeles en lugar de nm | El Shamrock no se abrió (mira el registro); revisa el cable o las DLL del espectrógrafo. |
| «No se detecta ninguna cámara» | CamExpert abierto, firewall, IP de la cámara fuera de la subred, falta el `.cti`. |
| El vídeo va a saltos | Jumbo frames, `packet_size`, tarjeta de red dedicada, driver de filtro GigE. |
| No deja adquirir | El CCD aún no está «Estable». Para pruebas, marca «Permitir sin CCD estable». |

Todo lo que aparece en el registro se guarda también en `raman_control.log`.
