import asyncio
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from bleak import BleakClient, BleakScanner

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    TRAY_AVAILABLE = True
    TRAY_IMPORT_ERROR = ""
except Exception as e:
    TRAY_AVAILABLE = False
    TRAY_IMPORT_ERROR = repr(e)


DEVICE_NAME_HINT = "Razer Stereo"
RAZER_SERVICE_UUID = "0000fd65-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "416d0000-2d52-617a-6572-424c4501f40a"
NOTIFY_UUID = "416d0001-2d52-617a-6572-424c4501f40a"

SCAN_TIMEOUT = 5.0
MONITOR_INTERVAL_MS = 8000

MODES = {
    "off": bytes.fromhex("92 00 01 00"),
    "anc": bytes.fromhex("92 00 01 01"),
    "transparency": bytes.fromhex("92 00 01 02"),
}

MODE_LABELS = {
    "off": "Выключено",
    "anc": "Шумоподавление",
    "transparency": "Прозрачность",
}

MODE_BY_NOTIFICATION = {
    (0x12, 0x02, 0x01, 0x00): "off",
    (0x12, 0x02, 0x01, 0x01): "anc",
    (0x12, 0x02, 0x01, 0x02): "transparency",
}


def resource_path(relative_path: str) -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class BleWorker:
    def __init__(self):
        self.client: BleakClient | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.current_address = ""
        self.connected = False
        self.notifying = False
        self.current_mode = "unknown"

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_coro(self, coro):
        if not self.loop:
            raise RuntimeError("BLE loop is not started")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _push(self, kind: str, value=None):
        self.events.put((kind, value))

    def _cleanup_state(self):
        self.client = None
        self.connected = False
        self.notifying = False

    def _parse_mode_from_notification(self, data: bytearray) -> str | None:
        if len(data) < 4:
            return None
        return MODE_BY_NOTIFICATION.get((data[0], data[1], data[2], data[3]))

    def _on_notification(self, _sender, data: bytearray):
        self._push("log", f"Уведомление: {data.hex(' ')}")

        mode = self._parse_mode_from_notification(data)
        if mode:
            self.current_mode = mode
            self._push("mode", mode)

    async def discover_razer_devices(self) -> list[tuple[str, str]]:
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT, return_adv=True)
        found: list[tuple[str, str]] = []

        for _addr, item in devices.items():
            device, adv = item
            name = (device.name or adv.local_name or "").strip()
            service_uuids = [u.lower() for u in (adv.service_uuids or [])]

            is_razer = (
                RAZER_SERVICE_UUID.lower() in service_uuids
                or DEVICE_NAME_HINT.lower() in name.lower()
            )

            if is_razer:
                found.append(("Razer наушники", device.address))

        unique = []
        seen = set()
        for name, address in found:
            if address not in seen:
                seen.add(address)
                unique.append((name, address))

        return unique

    async def _validate_razer_characteristics(self) -> None:
        all_chars: list[str] = []
        services = self.client.services if self.client else None

        if services:
            for service in services:
                for char in service.characteristics:
                    all_chars.append(char.uuid.lower())

        if not all_chars and self.client is not None:
            try:
                services = await self.client.get_services()
                for service in services:
                    for char in service.characteristics:
                        all_chars.append(char.uuid.lower())
            except Exception:
                pass

        if WRITE_UUID.lower() in all_chars and NOTIFY_UUID.lower() in all_chars:
            return

        available = "\n".join(sorted(all_chars)) if all_chars else "<характеристики не найдены>"

        try:
            if self.client and self.client.is_connected:
                await self.client.disconnect()
        except Exception:
            pass

        self._cleanup_state()

        raise RuntimeError(
            "Подключено не то BLE-устройство.\n\n"
            f"Текущий адрес: {self.current_address}\n"
            f"Ожидаются характеристики Razer:\n- {WRITE_UUID}\n- {NOTIFY_UUID}\n\n"
            f"Найденные характеристики:\n{available}\n\n"
            "Выбери именно наушники Razer."
        )

    async def connect(self, address: str) -> bool:
        old_address = self.current_address

        if self.client and self.client.is_connected:
            if address == old_address:
                self.connected = True
                self._push("connected", address)
                return True
            await self.disconnect()

        self.current_address = address
        self._push("connecting", address)

        self.client = BleakClient(address)
        await self.client.connect(timeout=10.0)

        self.connected = bool(self.client.is_connected)
        if not self.connected:
            self._push("error", "Не удалось подключиться")
            return False

        await self._validate_razer_characteristics()

        if not self.notifying:
            await self.client.start_notify(NOTIFY_UUID, self._on_notification)
            self.notifying = True

        self._push("connected", address)
        return True

    async def connect_best_available(self) -> bool:
        candidates: list[str] = []

        if self.current_address:
            candidates.append(self.current_address)

        try:
            found = await self.discover_razer_devices()
        except Exception as e:
            self._push("log", f"Ошибка поиска наушников: {e}")
            found = []

        for _name, address in found:
            if address not in candidates:
                candidates.append(address)

        if not candidates:
            self._push("log", "Автоподключение: наушники не найдены")
            return False

        for address in candidates:
            try:
                self._push("address", address)
                ok = await self.connect(address)
                if ok:
                    return True
            except Exception as e:
                self._push("log", f"Не удалось подключиться к {address}: {e}")
                try:
                    if self.client and self.client.is_connected:
                        await self.client.disconnect()
                except Exception:
                    pass
                self._cleanup_state()

        self._push("error", "Не удалось найти рабочее подключение к Razer-наушникам")
        return False

    async def disconnect(self):
        if self.client and self.client.is_connected:
            try:
                if self.notifying:
                    await self.client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass

            try:
                await self.client.disconnect()
            except Exception:
                pass

        self._cleanup_state()
        self._push("disconnected")

    async def set_mode(self, mode: str):
        if mode not in MODES:
            raise ValueError(f"Неизвестный режим: {mode}")

        if not self.client or not self.client.is_connected:
            raise RuntimeError("Сначала нужно подключиться к наушникам")

        cmd = MODES[mode]
        await self.client.write_gatt_char(WRITE_UUID, cmd, response=True)

        self.current_mode = mode
        self._push("mode", mode)
        self._push("log", f"Режим применён: {MODE_LABELS[mode]} ({cmd.hex(' ')})")
        return True

    async def poll_connection(self) -> bool:
        alive = bool(self.client and self.client.is_connected)

        if not alive:
            if self.connected or self.notifying or self.client is not None:
                self._cleanup_state()
                self._push("disconnected")
            return False

        self.connected = True
        return True


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Razer Opus X Control")
        self.root.geometry("820x470")
        self.root.minsize(820, 470)

        self._is_exiting = False
        self.hidden_to_tray = False

        try:
            self.root.iconbitmap(resource_path("razer.ico"))
        except Exception as e:
            print(f"Не удалось установить иконку окна: {e}")

        self.worker = BleWorker()
        self.worker.start()

        self.address_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Готово")
        self.connection_state_var = tk.StringVar(value="Не подключено")
        self.auto_connect_var = tk.BooleanVar(value=True)
        self.auto_reconnect_var = tk.BooleanVar(value=True)
        self.tray_enabled_var = tk.BooleanVar(value=TRAY_AVAILABLE)

        self.tray_icon = None
        self.monitor_job = None

        self._build_ui()
        self._set_mode_buttons_state(False)
        self._poll_events()
        self._init_tray_if_possible()
        self._start_background_jobs()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self._on_root_unmap)

        if self.auto_connect_var.get():
            self.root.after(700, self.auto_connect_startup)

    def _build_ui(self):
        top = tk.Frame(self.root, padx=12, pady=12)
        top.pack(fill="x")

        tk.Label(top, text="Адрес устройства:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.address_var, width=24, state="readonly").grid(row=0, column=1, sticky="w", padx=(8, 8))
        tk.Label(top, textvariable=self.connection_state_var, fg="#555").grid(row=0, column=2, sticky="w", padx=(0, 10))

        self.connect_btn = tk.Button(top, text="Подключить", width=14, command=self.connect_device)
        self.connect_btn.grid(row=0, column=3, padx=4)

        self.disconnect_btn = tk.Button(top, text="Отключить", width=14, command=self.disconnect_device)
        self.disconnect_btn.grid(row=0, column=4, padx=4)

        options_frame = tk.LabelFrame(self.root, text="Опции", padx=10, pady=10)
        options_frame.pack(fill="x", padx=12, pady=(0, 12))

        tk.Checkbutton(
            options_frame,
            text="Автоподключение при запуске",
            variable=self.auto_connect_var
        ).grid(row=0, column=0, sticky="w")

        tk.Checkbutton(
            options_frame,
            text="Автовосстановление соединения",
            variable=self.auto_reconnect_var
        ).grid(row=0, column=1, sticky="w", padx=(20, 0))

        tk.Checkbutton(
            options_frame,
            text="Трей Windows",
            variable=self.tray_enabled_var,
            state="normal" if TRAY_AVAILABLE else "disabled"
        ).grid(row=0, column=2, sticky="w", padx=(20, 0))

        modes = tk.LabelFrame(self.root, text="Режим", padx=12, pady=12)
        modes.pack(fill="x", padx=12, pady=(0, 12))

        btn_frame = tk.Frame(modes)
        btn_frame.pack(fill="x")

        self.off_btn = tk.Button(btn_frame, text="Выключено", width=18, height=2, command=lambda: self.send_mode("off"))
        self.off_btn.grid(row=0, column=0, padx=4, pady=4)

        self.anc_btn = tk.Button(btn_frame, text="Шумоподавление", width=18, height=2, command=lambda: self.send_mode("anc"))
        self.anc_btn.grid(row=0, column=1, padx=4, pady=4)

        self.trans_btn = tk.Button(btn_frame, text="Прозрачность", width=18, height=2, command=lambda: self.send_mode("transparency"))
        self.trans_btn.grid(row=0, column=2, padx=4, pady=4)

        log_frame = tk.LabelFrame(self.root, text="Журнал", padx=10, pady=10)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.log = tk.Text(log_frame, height=14, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

        bottom = tk.Frame(self.root, padx=12, pady=8)
        bottom.pack(fill="x")

        tk.Label(bottom, textvariable=self.status_var, anchor="w").pack(side="left")
        tk.Button(bottom, text="Очистить журнал", command=self.clear_log).pack(side="right")

    def _set_mode_buttons_state(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.off_btn.configure(state=state)
        self.anc_btn.configure(state=state)
        self.trans_btn.configure(state=state)

    def _start_background_jobs(self):
        self.schedule_monitor()

    def schedule_monitor(self):
        self.monitor_job = self.root.after(MONITOR_INTERVAL_MS, self.monitor_connection)

    def auto_connect_startup(self):
        self.append_log("Автоподключение при запуске...")
        future = self.worker.run_coro(self.worker.connect_best_available())
        future.add_done_callback(self._future_error_handler(show_popup=False))

    def monitor_connection(self):
        if self._is_exiting:
            return

        if self.connection_state_var.get() == "Идёт подключение...":
            self.schedule_monitor()
            return

        future = self.worker.run_coro(self.worker.poll_connection())

        def done_callback(fut):
            try:
                alive = bool(fut.result())
            except Exception as e:
                self.root.after(0, lambda err=e: self._handle_error(str(err), popup=False))
                self.root.after(0, self.schedule_monitor)
                return

            def continue_in_ui():
                if not alive and self.auto_reconnect_var.get():
                    self.append_log("Соединение потеряно, пробую переподключиться...")
                    reconnect_future = self.worker.run_coro(self.worker.connect_best_available())
                    reconnect_future.add_done_callback(self._future_error_handler(show_popup=False))

                self.schedule_monitor()

            self.root.after(0, continue_in_ui)

        future.add_done_callback(done_callback)

    def _future_error_handler(self, show_popup: bool):
        def done_callback(fut):
            try:
                fut.result()
            except Exception as e:
                self.root.after(0, lambda err=e: self._handle_error(str(err), popup=show_popup))
        return done_callback

    def _poll_events(self):
        if self._is_exiting:
            return

        while True:
            try:
                kind, value = self.worker.events.get_nowait()
            except queue.Empty:
                break
            else:
                if kind == "log":
                    self.append_log(str(value))
                    self.status_var.set(str(value))
                    continue

                if kind == "address":
                    self.address_var.set(str(value))
                    continue

                if kind == "connecting":
                    self.address_var.set(str(value))
                    self.connection_state_var.set("Идёт подключение...")
                    self.connect_btn.configure(state="disabled")
                    self._set_mode_buttons_state(False)
                    self.status_var.set(f"Подключение к {value}...")
                    continue

                if kind == "connected":
                    self.address_var.set(str(value))
                    self.connection_state_var.set("Подключено")
                    self.connect_btn.configure(state="normal")
                    self._set_mode_buttons_state(True)
                    self.status_var.set("Подключено")
                    continue

                if kind == "disconnected":
                    self.connection_state_var.set("Не подключено")
                    self.connect_btn.configure(state="normal")
                    self._set_mode_buttons_state(False)
                    self.status_var.set("Отключено")
                    continue

                if kind == "mode":
                    continue

                if kind == "error":
                    self._handle_error(str(value), popup=False)
                    continue

        self.root.after(150, self._poll_events)

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def connect_device(self):
        self.connection_state_var.set("Идёт подключение...")
        self.connect_btn.configure(state="disabled")
        self._set_mode_buttons_state(False)
        future = self.worker.run_coro(self.worker.connect_best_available())
        future.add_done_callback(self._future_error_handler(show_popup=False))

    def disconnect_device(self):
        self.connection_state_var.set("Отключение...")
        self._set_mode_buttons_state(False)
        future = self.worker.run_coro(self.worker.disconnect())
        future.add_done_callback(self._future_error_handler(show_popup=False))

    def send_mode(self, mode: str):
        if self.connection_state_var.get() != "Подключено":
            self.append_log("Ошибка: сначала нужно успешно подключиться")
            return
        future = self.worker.run_coro(self.worker.set_mode(mode))
        future.add_done_callback(self._future_error_handler(show_popup=False))

    def _handle_error(self, message: str, popup: bool = False):
        self.append_log(f"Ошибка: {message}")
        self.status_var.set("Ошибка")
        self.connection_state_var.set("Не подключено")
        self.connect_btn.configure(state="normal")
        self._set_mode_buttons_state(False)

        if popup:
            try:
                messagebox.showerror("Ошибка", message)
            except Exception:
                pass

    def _get_tray_font(self, size: int):
        if not TRAY_AVAILABLE:
            return None

        for font_name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(font_name, size)
            except Exception:
                pass

        try:
            return ImageFont.load_default()
        except Exception:
            return None

    def _create_fallback_tray_image(self):
        image = Image.new("RGB", (64, 64), (18, 18, 18))
        draw = ImageDraw.Draw(image)
        font = self._get_tray_font(24)

        text = "RX"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        x = (64 - text_w) // 2
        y = (64 - text_h) // 2 - 2

        draw.rectangle((2, 2, 61, 61), outline=(0, 160, 90), width=2)
        draw.text((x, y), text, fill=(0, 255, 110), font=font)
        return image

    def _create_tray_image(self):
        if not TRAY_AVAILABLE:
            return None

        icon_path = resource_path("razer.ico")
        if os.path.exists(icon_path):
            try:
                image = Image.open(icon_path).convert("RGBA")
                resampling = getattr(Image, "Resampling", Image)
                lanczos = getattr(resampling, "LANCZOS", Image.LANCZOS)
                image = image.resize((64, 64), lanczos)
                return image
            except Exception as e:
                self.append_log(f"Не удалось загрузить razer.ico для трея: {e}")

        return self._create_fallback_tray_image()

    def _tray_title(self):
        state = self.connection_state_var.get()
        return f"Razer Opus X Control — {state}"

    def _update_tray_icon(self):
        if not TRAY_AVAILABLE or self.tray_icon is None:
            return
        try:
            self.tray_icon.icon = self._create_tray_image()
            self.tray_icon.title = self._tray_title()
        except Exception as e:
            self.append_log(f"Ошибка обновления иконки трея: {e}")

    def _init_tray_if_possible(self):
        if not TRAY_AVAILABLE:
            self.append_log(f"Трей недоступен: {TRAY_IMPORT_ERROR}")
            return

        try:
            image = self._create_tray_image()
            menu = pystray.Menu(
                pystray.MenuItem("Показать", self._tray_show, default=True),
                pystray.MenuItem("Шумоподавление", self._tray_anc),
                pystray.MenuItem("Прозрачность", self._tray_transparency),
                pystray.MenuItem("Выключено", self._tray_off),
                pystray.MenuItem("Выход", self._tray_exit),
            )

            self.tray_icon = pystray.Icon(
                "razer_opus_x",
                image,
                self._tray_title(),
                menu
            )

            self.tray_icon.run_detached()
            self.append_log("Трей успешно инициализирован")
        except Exception as e:
            self.tray_icon = None
            self.append_log(f"Ошибка инициализации трея: {e}")

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self._show_window)

    def _tray_anc(self, icon=None, item=None):
        self.root.after(0, lambda: self.send_mode("anc"))

    def _tray_transparency(self, icon=None, item=None):
        self.root.after(0, lambda: self.send_mode("transparency"))

    def _tray_off(self, icon=None, item=None):
        self.root.after(0, lambda: self.send_mode("off"))

    def _show_window(self):
        self.hidden_to_tray = False
        self.root.deiconify()
        self.root.state("normal")
        self.root.after(50, self.root.lift)
        self.root.after(100, lambda: self.root.attributes("-topmost", True))
        self.root.after(150, lambda: self.root.attributes("-topmost", False))
        self.status_var.set("Окно восстановлено")
        self._update_tray_icon()

    def _hide_to_tray(self):
        if self._is_exiting:
            return
        if not self.tray_enabled_var.get() or self.tray_icon is None:
            return
        if self.hidden_to_tray:
            return

        self.hidden_to_tray = True
        self.root.withdraw()
        self.status_var.set("Скрыто в трей")
        self.append_log("Окно скрыто в трей")
        self._update_tray_icon()

    def _on_root_unmap(self, _event=None):
        if self._is_exiting:
            return
        if not self.tray_enabled_var.get() or self.tray_icon is None:
            return

        try:
            if self.root.state() == "iconic":
                self.root.after(0, self._hide_to_tray)
        except tk.TclError:
            pass

    def _tray_exit(self, icon=None, item=None):
        self.root.after(0, self._exit_app)

    def _exit_app(self):
        if self._is_exiting:
            return

        self._is_exiting = True

        try:
            if self.monitor_job is not None:
                self.root.after_cancel(self.monitor_job)
                self.monitor_job = None
        except Exception:
            pass

        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass

        try:
            if self.worker.loop and self.worker.loop.is_running():
                try:
                    self.worker.run_coro(self.worker.disconnect())
                except Exception:
                    pass
                self.worker.loop.call_soon_threadsafe(self.worker.loop.stop)
        except Exception:
            pass

        self.root.destroy()

    def on_close(self):
        if self.tray_enabled_var.get() and self.tray_icon is not None:
            self._hide_to_tray()
        else:
            self._exit_app()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()