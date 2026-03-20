# Razer Opus X Control (BLE)

Desktop GUI to control **Razer Opus X** headphones over Bluetooth Low Energy (BLE). Connect to the device, then switch audio modes like ANC and transparency.

## Features

- BLE discovery + auto-connect to a Razer headset
- Mode switching:
  - `off` (disabled)
  - `anc` (active noise cancellation)
  - `transparency`
- Tray icon support on Windows (optional)
- Live connection monitoring + auto-reconnect option

## Requirements

- Windows 10/11
- Python **3.10+**
- Bluetooth enabled on the computer
- Compatible Razer device advertising the expected BLE service characteristics

## Installation

1. Create and activate a virtual environment (recommended)
   - Example (PowerShell):
     - `python -m venv .venv`
     - `.venv\\Scripts\\Activate.ps1`
2. Install dependencies:
   - `pip install -r requirements.txt`

## Usage

1. Start the application:
   - `python main.py`
2. The GUI will attempt to connect automatically (if enabled).
3. Once connected, use the mode buttons:
   - “Шумоподавление” (ANC)
   - “Прозрачность” (Transparency)
   - “Выключено” (Off)
4. (Optional) Enable “Трей Windows” to control modes from the system tray.

## Notes

- The application validates that the connected BLE device exposes the expected Razer characteristics before enabling controls.
- If tray support is unavailable on your system, the app continues to run without it.

