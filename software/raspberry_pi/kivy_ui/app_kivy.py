#!/usr/bin/env python3
import time
import json
import os
#import serial

from kivy.config import Config
Config.set("graphics", "fullscreen", "1")
Config.set("graphics", "borderless", "1")
Config.set("graphics", "resizable", "0")
Config.set("graphics", "width", "800")
Config.set("graphics", "height", "480")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.slider import Slider
from kivy.uix.gridlayout import GridLayout
from kivy.graphics import Color, Rectangle


FAN_PORT = "/dev/atm_fan"
IO_PORT  = "/dev/atm_io"
BAUD = 9600

PRESETS_PATH = "presets.json"


class SerialLink:
    """Minimal serial helper with non-blocking line polling and lazy reconnect."""
    def __init__(self, port: str, baud: int, name: str):
        self.port = port
        self.baud = baud
        self.name = name
        self.ser = None
        self.last_err = ""

    def open(self) -> None:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            time.sleep(2)  # Arduino reset window
            self.ser.reset_input_buffer()
            self.last_err = ""
        except Exception as e:
            self.ser = None
            self.last_err = str(e)

    def is_open(self) -> bool:
        return self.ser is not None and getattr(self.ser, "is_open", False)

    def close(self) -> None:
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def write_line(self, line: str) -> bool:
        if not self.is_open():
            self.open()
        if not self.is_open():
            return False
        try:
            self.ser.write((line.strip() + "\n").encode("ascii", errors="ignore"))
            self.ser.flush()
            return True
        except Exception as e:
            self.last_err = str(e)
            self.close()
            return False

    def read_lines(self, max_lines: int = 30):
        out = []
        if not self.is_open():
            return out
        try:
            for _ in range(max_lines):
                if self.ser.in_waiting <= 0:
                    break
                raw = self.ser.readline()
                if not raw:
                    break
                line = raw.decode(errors="ignore").strip()
                if line:
                    out.append(line)
        except Exception as e:
            self.last_err = str(e)
            self.close()
        return out


def mk_lbl(txt, size=14, bold=False, width=None, align="left", color=(0, 0, 0, 1), height=28):
    lbl = Label(
        text=txt,
        font_size=f"{size}sp",
        bold=bold,
        color=color,
        halign=align,
        valign="middle",
        size_hint_x=(None if width is not None else 1),
        width=(dp(width) if width is not None else 0),
        size_hint_y=None,
        height=dp(height),
    )
    lbl.bind(size=lambda *_: setattr(lbl, "text_size", lbl.size))
    return lbl


def section_title(text: str):
    return mk_lbl(text, size=13, bold=True, color=(0.1, 0.1, 0.1, 1), height=22)


class Panel(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", spacing=dp(4), padding=dp(4), **kwargs)

        # Background
        with self.canvas.before:
            Color(0.95, 0.95, 0.95, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._bg_update, size=self._bg_update)

        # Serial links
        self.fan_link = SerialLink(FAN_PORT, BAUD, "FAN")
        self.io_link  = SerialLink(IO_PORT,  BAUD, "IO")

        # State
        self.fan_vals = [0] * 8
        self.pump_pwm = 0
        self.heat_on = False
        self.fog_on  = False

        self._send_ev = None
        self._poll_ev = None
        self._toggle_lock_until = 0.0

        # Presets
        self.presets = {i: None for i in range(1, 6)}
        self.selected_preset = 1
        self._suspend_ui_callbacks = False
        self._load_presets()

        # UI references (to set values during preset apply / reset)
        self.fan_sliders = []
        self.fan_value_labels = []
        self.pump_slider = None
        self.pump_value_label = None
        self.preset_btns = {}
        self.sensor_rows = {}

        # ---------- TOP BAR ----------
        top = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6), padding=dp(4))
        top.add_widget(mk_lbl("OKATEM Atmospheric Chamber Control Panel", size=22, bold=True, height=42))

        btn_reset = Button(
            text="Reset",
            size_hint_x=None,
            width=dp(120),
            background_normal="",
            background_color=(1.0, 0.95, 0.75, 1),
            color=(0, 0, 0, 1),
        )
        btn_reset.bind(on_release=lambda *_: self._all_off())

        btn_exit = Button(
            text="Exit",
            size_hint_x=None,
            width=dp(120),
            background_normal="",
            background_color=(1.0, 0.85, 0.85, 1),
            color=(0, 0, 0, 1),
        )
        btn_exit.bind(on_release=self._exit_app)

        top.add_widget(btn_reset)
        top.add_widget(btn_exit)
        self.add_widget(top)

        # ---------- STATUS BAR ----------
        st = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6), padding=dp(4))
        self.lbl_fan = mk_lbl(f"FAN: not connected ({FAN_PORT})", size=11, height=24)
        self.lbl_io  = mk_lbl(f"IO: not connected ({IO_PORT})", size=11, height=24)
        st.add_widget(self.lbl_fan)
        st.add_widget(self.lbl_io)
        self.add_widget(st)

        # ---------- MAIN (2 COLUMNS) ----------
        main = BoxLayout(orientation="horizontal", spacing=dp(8))
        main.add_widget(self._make_left_controls())
        main.add_widget(self._make_right_sensors())
        self.add_widget(main)
        self.add_widget(mk_lbl("", height=dp(18)))  # spacer
        # schedule poll
        self._poll_ev = Clock.schedule_interval(self._poll_serial, 0.2)
        Clock.schedule_once(self._fix_top_gap, 0)

    # ---------------- UI helpers ----------------
    def _bg_update(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _fix_top_gap(self, *_):
        try:
            gap = int(Window.height - self.height)
            if gap > 0:
                l, t, r, b = self.padding
                self.padding = [l, max(0, int(t) - gap), r, b]
        except Exception:
            pass

    def on_parent(self, instance, parent):
        # Clean shutdown
        if parent is None:
            try:
                if self._send_ev is not None:
                    self._send_ev.cancel()
            except Exception:
                pass
            try:
                if self._poll_ev is not None:
                    self._poll_ev.cancel()
            except Exception:
                pass
            self.fan_link.close()
            self.io_link.close()

    def _exit_app(self, *_):
        app = App.get_running_app()
        if app is not None:
            app.stop()

    # ---------------- Layout builders ----------------
    def _make_left_controls(self):
        left = BoxLayout(orientation="vertical", size_hint_x=0.6, spacing=dp(4), padding=dp(12))

        left.add_widget(section_title("Fans and Pump (UI 0-100)"))

        fan_grid = GridLayout(cols=2, spacing=dp(4), size_hint_y=None)
        fan_grid.bind(minimum_height=fan_grid.setter("height"))

        for i in range(8):
            row, s, v = self._make_fan_row(i, compact=True)
            self.fan_sliders.append(s)
            self.fan_value_labels.append(v)
            fan_grid.add_widget(row)

        left.add_widget(fan_grid)

        prow, ps, pv = self._make_pump_row(compact=True)
        self.pump_slider = ps
        self.pump_value_label = pv
        left.add_widget(prow)

        left.add_widget(section_title("Toggles"))
        tog = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))

        self.btn_heat = Button(
            text="Heater: OFF",
            background_normal="",
            background_color=(0.85, 0.90, 1.0, 1),
            color=(0, 0, 0, 1),
        )
        self.btn_fog = Button(
            text="Fogger: OFF",
            background_normal="",
            background_color=(0.85, 0.90, 1.0, 1),
            color=(0, 0, 0, 1),
        )

        self.btn_heat.bind(on_release=self._toggle_heat)
        self.btn_fog.bind(on_release=self._toggle_fog)

        tog.add_widget(self.btn_heat)
        tog.add_widget(self.btn_fog)
        left.add_widget(tog)

        # Presets row (1..5 + Apply + Save)
        left.add_widget(section_title("Presets"))
        left.add_widget(self._make_presets_row())

        return left

    def _make_presets_row(self):
        row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(6))

        # 1..5 selector buttons
        sel = BoxLayout(size_hint_x=0.3, spacing=dp(4))
        for i in range(1, 6):
            b = Button(
                text=str(i),
                background_normal="",
                background_color=(0.90, 0.90, 0.90, 1),
                color=(0, 0, 0, 1),
            )
            b.bind(on_release=lambda _btn, idx=i: self._select_preset(idx))
            self.preset_btns[i] = b
            sel.add_widget(b)

        row.add_widget(sel)

        btn_apply = Button(
            text="Apply",
            size_hint_x=0.1,
            background_normal="",
            background_color=(0.80, 0.95, 0.80, 1),
            color=(0, 0, 0, 1),
        )
        btn_apply.bind(on_release=lambda *_: self._apply_preset(self.selected_preset))

        btn_save = Button(
            text="Save",
            size_hint_x=0.1,
            background_normal="",
            background_color=(0.80, 0.85, 1.00, 1),
            color=(0, 0, 0, 1),
        )
        btn_save.bind(on_release=lambda *_: self._save_preset(self.selected_preset))

        row.add_widget(btn_save)
        row.add_widget(btn_apply)

        self._refresh_preset_buttons()
        return row

    def _make_right_sensors(self):
        right = BoxLayout(orientation="vertical", size_hint_x=0.25, spacing=dp(2))
        right.add_widget(section_title("Sensors"))
        right.alignment = "top"

        grid = GridLayout(cols=1, spacing=dp(2), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))

        for key, title in [
            ("T1", "Temp 1 (°C)"),
            ("T2", "Temp 2 (°C)"),
            ("T3", "Temp 3 (°C)"),
            ("T4", "Temp 4 (°C)"),
            ("TAMB", "Temp 5 (°C)"),
            ("H", "Humidity (%)"),
            ("DUSTMG", "Dust (mg/m³)"),
        ]:
            r = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(4))
            r.add_widget(mk_lbl(title, size=12))
            v = mk_lbl("--", size=12, bold=True, width=110, align="right")
            r.add_widget(v)
            self.sensor_rows[key] = v
            grid.add_widget(r)
        
        right.add_widget(grid)
        right.add_widget(mk_lbl("", height=dp(120)))  # spacer

        return right

    def _make_fan_row(self, idx, compact=False):
        h = dp(36) if compact else dp(48)
        row = BoxLayout(size_hint_y=None, height=h, spacing=dp(4))
        row.add_widget(mk_lbl(f"Fan{idx+1}", size=12, bold=True, width=32))

        s = Slider(min=0, max=100, value=0, height=dp(24) if compact else dp(32))
        v = mk_lbl("0", size=12, bold=True, width=40, align="right")

        def _on(_inst, val):
            iv = max(0, min(100, int(val)))
            self.fan_vals[idx] = iv
            v.text = str(iv)
            if self._suspend_ui_callbacks:
                return
            self._schedule_send()

        s.bind(value=_on)
        row.add_widget(s)
        row.add_widget(v)
        return row, s, v

    def _make_pump_row(self, compact=False):
        h = dp(28) if compact else dp(36)
        row = BoxLayout(size_hint_y=None, height=h, spacing=dp(4))
        row.add_widget(mk_lbl("Pump", size=12, bold=True, width=32))

        s = Slider(min=0, max=100, value=0)
        v = mk_lbl("0", size=12, bold=True, width=40, align="right")

        def _on(_inst, val):
            iv = max(0, min(100, int(val)))
            self.pump_pwm = iv
            v.text = str(iv)
            if self._suspend_ui_callbacks:
                return
            self._schedule_send()

        s.bind(value=_on)
        row.add_widget(s)
        row.add_widget(v)
        return row, s, v

    # ---------------- Presets logic ----------------
    def _select_preset(self, idx: int):
        self.selected_preset = int(idx)
        self._refresh_preset_buttons()

    def _refresh_preset_buttons(self):
        for i, b in self.preset_btns.items():
            if i == self.selected_preset:
                b.background_color = (0.75, 0.85, 1.0, 1)
            else:
                b.background_color = (0.90, 0.90, 0.90, 1)

    def _persist_presets(self):
        try:
            folder = os.path.dirname(PRESETS_PATH)
            if folder:
                os.makedirs(folder, exist_ok=True)
            data = {
                "version": 1,
                "saved_at": time.time(),
                "presets": self.presets,
            }
            tmp = PRESETS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, PRESETS_PATH)
        except Exception as e:
            self.lbl_io.text = f"IO: preset save ERROR | {e}"

    def _load_presets(self):
        try:
            if not os.path.exists(PRESETS_PATH):
                return
            with open(PRESETS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            presets = data.get("presets", {})
            for i in range(1, 6):
                p = presets.get(str(i), presets.get(i))
                if isinstance(p, dict) and all(k in p for k in ("fans", "pump", "heat", "fog")):
                    self.presets[i] = p
        except Exception:
            self.presets = {i: None for i in range(1, 6)}

    def _save_preset(self, idx: int):
        fan = [int(v) for v in self.fan_vals]
        if len(fan) != 8:
            fan = [0] * 8
        pump = max(0, min(100, int(self.pump_pwm)))

        self.presets[int(idx)] = {
            "fans": fan,
            "pump": pump,
            "heat": bool(self.heat_on),
            "fog": bool(self.fog_on),
            "ts": time.time(),
        }
        self._persist_presets()
        self.lbl_io.text = f"IO: preset {idx} saved"

    def _apply_preset(self, idx: int):
        p = self.presets.get(int(idx))
        if not p:
            self.lbl_io.text = f"IO: preset {idx} is empty"
            return

        self._suspend_ui_callbacks = True
        try:
            # Fans
            fans = p.get("fans", [0]*8)
            for i in range(8):
                v = max(0, min(100, int(fans[i])))
                self.fan_vals[i] = v
                self.fan_sliders[i].value = v
                self.fan_value_labels[i].text = str(v)

            # Pump
            pv = max(0, min(100, int(p.get("pump", 0))))
            self.pump_pwm = pv
            if self.pump_slider is not None:
                self.pump_slider.value = pv
            if self.pump_value_label is not None:
                self.pump_value_label.text = str(pv)

            # Toggles
            self.heat_on = bool(p.get("heat", False))
            self.fog_on  = bool(p.get("fog", False))
            self._refresh_toggle_buttons()
        finally:
            self._suspend_ui_callbacks = False

        # Send once
        self._send_fan_state()
        self._send_io_cmd("HEAT", self.heat_on)
        self._send_io_cmd("FOG", self.fog_on)
        self.lbl_io.text = f"IO: preset {idx} applied"

    # ---------------- Toggles / Reset ----------------
    def _toggle_guard(self):
        now = time.time()
        if now < self._toggle_lock_until:
            return False
        self._toggle_lock_until = now + 0.25
        return True

    def _refresh_toggle_buttons(self):
        self.btn_heat.text = f"Heater: {'ON' if self.heat_on else 'OFF'}"
        self.btn_heat.background_color = (0.80, 1.00, 0.80, 1) if self.heat_on else (0.85, 0.90, 1.0, 1)
        self.btn_fog.text = f"Fogger: {'ON' if self.fog_on else 'OFF'}"
        self.btn_fog.background_color = (0.80, 1.00, 0.80, 1) if self.fog_on else (0.85, 0.90, 1.0, 1)

    def _toggle_heat(self, *_):
        if not self._toggle_guard():
            return
        self.heat_on = not self.heat_on
        self._refresh_toggle_buttons()
        self._send_io_cmd("HEAT", self.heat_on)

    def _toggle_fog(self, *_):
        if not self._toggle_guard():
            return
        self.fog_on = not self.fog_on
        self._refresh_toggle_buttons()
        self._send_io_cmd("FOG", self.fog_on)

    def _all_off(self):
        self._suspend_ui_callbacks = True
        try:
            for i in range(8):
                self.fan_vals[i] = 0
                self.fan_sliders[i].value = 0
                self.fan_value_labels[i].text = "0"
            self.pump_pwm = 0
            if self.pump_slider is not None:
                self.pump_slider.value = 0
            if self.pump_value_label is not None:
                self.pump_value_label.text = "0"
            self.heat_on = False
            self.fog_on = False
            self._refresh_toggle_buttons()
        finally:
            self._suspend_ui_callbacks = False

        self._send_fan_state()
        self._send_io_cmd("HEAT", False)
        self._send_io_cmd("FOG", False)
        self.lbl_io.text = "IO: ALL OFF sent"

    # ---------------- Fan mapping + send ----------------
    def _map_fan_pwm(self, ui_val: int) -> int:
        # UI: 0-100  ->  Arduino: 0 veya 75-255 (0 özel durum)
        if ui_val <= 0:
            return 0
        return int(75 + (ui_val / 100.0) * (255 - 75))

    def _map_pump_pwm(self, ui_val: int) -> int:
        # UI: 0-100 -> Arduino: 0-255
        ui_val = max(0, min(100, int(ui_val)))
        return int((ui_val / 100.0) * 255)

    def _schedule_send(self):
        if self._send_ev is not None:
            try:
                self._send_ev.cancel()
            except Exception:
                pass
        self._send_ev = Clock.schedule_once(lambda *_: self._send_fan_state(), 0.2)

    def _send_fan_state(self):
        mapped = [self._map_fan_pwm(max(0, min(100, int(v)))) for v in self.fan_vals]
        p = self._map_pump_pwm(self.pump_pwm)

        cmd = " ".join([f"F{i+1}={mapped[i]}" for i in range(8)]) + f" P1={p}"
        ok = self.fan_link.write_line(cmd)
        if ok:
            self.lbl_fan.text = f"FAN: connected ({FAN_PORT}) | last: {cmd}"
        else:
            self.lbl_fan.text = f"FAN: ERROR ({FAN_PORT}) | {self.fan_link.last_err or 'not connected'}"

    def _send_io_cmd(self, key: str, on: bool):
        cmd = f"IO;{key}={1 if on else 0}"
        ok = self.io_link.write_line(cmd)
        if ok:
            self.lbl_io.text = f"IO: connected ({IO_PORT}) | last: {cmd}"
        else:
            self.lbl_io.text = f"IO: ERROR ({IO_PORT}) | {self.io_link.last_err or 'not connected'}"

    # ---------------- Parsing + poll ----------------
    def _parse_sens(self, line: str):
        parts = line.split(";")
        kv = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.strip()] = v.strip()
        for k, lbl in self.sensor_rows.items():
            if k in kv:
                lbl.text = kv[k]

    def _poll_serial(self, _dt):
        # Status if disconnected (lazy reconnect behavior)
        if not self.fan_link.is_open() and self.fan_link.last_err:
            self.lbl_fan.text = f"FAN: ERROR ({FAN_PORT}) | {self.fan_link.last_err}"
        elif not self.fan_link.is_open():
            self.lbl_fan.text = f"FAN: not connected ({FAN_PORT})"

        if not self.io_link.is_open() and self.io_link.last_err:
            self.lbl_io.text = f"IO: ERROR ({IO_PORT}) | {self.io_link.last_err}"
        elif not self.io_link.is_open():
            self.lbl_io.text = f"IO: not connected ({IO_PORT})"

        for line in self.io_link.read_lines(max_lines=30):
            if line.startswith("SENS;"):
                self._parse_sens(line)


class ATMApp(App):
    def build(self):
        root = Panel()

        def _fix(_dt):
            Window.borderless = True
            Window.fullscreen = 'auto'
            Window.left = 0
            Window.top = 0

        Clock.schedule_once(_fix, 0)
        return root


if __name__ == "__main__":
    ATMApp().run()
