#!/usr/bin/env python3
import time
import tkinter as tk
from tkinter import ttk

import serial
import json
import os


FAN_PORT = "/dev/atm_fan"
IO_PORT = "/dev/atm_io"
BAUD = 9600

# UI range (displayed)
UI_MAX = 100

# Fan effective PWM range on Arduino (hardware)
FAN_MIN_PWM = 75
FAN_MAX_PWM = 255

# Persist presets
PRESETS_PATH = "presets_old.json"


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
        """Write a single newline-terminated line. Returns True on success."""
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

    def read_lines(self, max_lines: int = 20):
        """Return up to max_lines decoded lines (non-blocking)."""
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ATM Chamber Control")
        self.geometry("800x480")
        self.attributes("-fullscreen", True)

        # Serial links
        self.fan_link = SerialLink(FAN_PORT, BAUD, "FAN")
        self.io_link = SerialLink(IO_PORT, BAUD, "IO")

        # ----- State vars -----
        self.heat_on = tk.BooleanVar(value=False)
        self.fog_on = tk.BooleanVar(value=False)

        self.status_fan = tk.StringVar(value=f"FAN: not connected ({FAN_PORT})")
        self.status_io = tk.StringVar(value=f"IO: not connected ({IO_PORT})")

        # Sensor display vars
        self.sensor_vars = {
            "Temp 1 (°C)": tk.StringVar(value="--"),
            "Temp 2 (°C)": tk.StringVar(value="--"),
            "Temp 3 (°C)": tk.StringVar(value="--"),
            "Temp 4 (°C)": tk.StringVar(value="--"),
            "Temp 5 (°C)": tk.StringVar(value="--"),
            "Humidity (%)": tk.StringVar(value="--"),
            "Dust (mg/m³)": tk.StringVar(value="--"),
        }

        self.presets = {i: None for i in range(1, 6)}  # 1..5
        self.selected_preset = tk.IntVar(value=1)
        self._load_presets()

        # IMPORTANT: prevent UI callbacks from firing during preset apply/reset
        self._suspend_ui_callbacks = False

        self._build_ui()

        # Start polling
        self.after(200, self._poll_serial)

    # ================= UI =================
    def _build_ui(self):
        header = ttk.Frame(self, padding=10)
        header.pack(fill="x")

        ttk.Label(header, text="ATM Chamber Control Panel", font=("Arial", 20, "bold")).pack(side="left")
        ttk.Button(header, text="Exit", command=self.destroy).pack(side="right")
        ttk.Button(header, text="Reset", command=self._all_off).pack(side="right", padx=(8, 0))

        status = ttk.Frame(self, padding=(10, 0, 10, 10))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_fan).pack(side="left", padx=(0, 20))
        ttk.Label(status, textvariable=self.status_io).pack(side="left")

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right.pack(side="right", fill="both", expand=True)

        # ---- FAN (8x PWM) + PUMP PWM ----
        fan_box = ttk.Labelframe(left, text="Fans (UI 0-100)", padding=12)
        fan_box.pack(fill="x", expand=False, pady=(0, 10))

        self.fan_vars = [tk.IntVar(value=0) for _ in range(8)]
        self._outputs_debounce_id = None

        def schedule_send_outputs(_evt=None):
            if self._outputs_debounce_id is not None:
                try:
                    self.after_cancel(self._outputs_debounce_id)
                except Exception:
                    pass
            self._outputs_debounce_id = self.after(200, self._send_fan_and_pump_state)

        # 2-column layout container
        grid = ttk.Frame(fan_box, padding=(0, 6))
        grid.pack(fill="x")

        # make columns stretch
        grid.columnconfigure(1, weight=3)
        grid.columnconfigure(3, weight=3)

        self.fan_scales = []
        self.fan_val_labels = []
        for i in range(8):
            r = i % 4              # 0..3
            c = 0 if i < 4 else 2  # left block or right block

            ttk.Label(grid, text=f"F{i+1}", font=("Arial", 12)).grid(
                row=r, column=c, sticky="w", padx=(0, 10), pady=(6, 6)
            )
            s = ttk.Scale(grid, from_=0, to=UI_MAX, orient="horizontal")
            s.grid(row=r, column=c + 1, sticky="ew", padx=(0, 10), pady=(6, 6))

            val_lbl = ttk.Label(grid, width=3, anchor="e", font=("Arial", 11, "bold"))
            val_lbl.grid(row=r, column=c + 1, sticky="e", padx=(0, 0))

            self.fan_scales.append(s)
            self.fan_val_labels.append(val_lbl)

            def _on_change(_v, idx=i, scale=s, lbl=val_lbl):
                iv = int(float(scale.get()))
                iv = max(0, min(UI_MAX, iv))
                self.fan_vars[idx].set(iv)
                lbl.config(text=str(iv))
                if self._suspend_ui_callbacks:
                    return
                schedule_send_outputs()

            s.set(0)
            val_lbl.config(text="0")
            s.configure(command=_on_change)

        # ---- PUMP (UI 0-100) ----
        pump_row = ttk.Frame(fan_box, padding=(0, 6))
        pump_row.pack(fill="x", pady=(8, 0))

        ttk.Label(pump_row, text="Pump (UI 0-100)", font=("Arial", 12)).pack(side="left")

        self.pump_pwm = tk.IntVar(value=0)

        pump_scale = ttk.Scale(pump_row, from_=0, to=UI_MAX, orient="horizontal")
        pump_scale.pack(side="left", fill="x", expand=True, padx=8)

        pump_val_lbl = ttk.Label(pump_row, width=4, anchor="e", font=("Arial", 12, "bold"))
        pump_val_lbl.pack(side="right")

        self.pump_scale = pump_scale
        self.pump_val_label = pump_val_lbl

        def _on_pump_pwm_change(_v):
            iv = int(float(pump_scale.get()))
            iv = max(0, min(UI_MAX, iv))
            self.pump_pwm.set(iv)
            pump_val_lbl.config(text=str(iv))
            if self._suspend_ui_callbacks:
                return
            schedule_send_outputs()

        pump_scale.set(0)
        pump_val_lbl.config(text="0")
        pump_scale.configure(command=_on_pump_pwm_change)

        # ---- IO ----
        io_box = ttk.Labelframe(left, text="IO (Sensors + Other Actuators)", padding=10)
        io_box.pack(fill="both", expand=True)

        io_toggles = ttk.Frame(io_box)
        io_toggles.pack(fill="x")

        # ---- Presets (single-row) ----
        presets_row = ttk.Frame(io_box, padding=(0, 10, 0, 0))
        presets_row.pack(fill="x")

        ttk.Label(presets_row, text="Presets:", font=("Arial", 12)).pack(side="left")

        for i in range(1, 6):
            ttk.Radiobutton(
                presets_row,
                text=str(i),
                variable=self.selected_preset,
                value=i
            ).pack(side="left", padx=2)

        ttk.Button(
            presets_row,
            text="Apply",
            command=self._apply_selected_preset
        ).pack(side="right", padx=(8, 0))

        ttk.Button(
            presets_row,
            text="Save Changes",
            command=self._open_save_preset_popup
        ).pack(side="right")

        self.heater_toggle = self._make_toggle(
            io_toggles, "Heater", self.heat_on,
            lambda: self._send_io_cmd("HEAT", self.heat_on.get())
        )
        self.heater_toggle.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self.fogger_toggle = self._make_toggle(
            io_toggles, "Fogger", self.fog_on,
            lambda: self._send_io_cmd("FOG", self.fog_on.get())
        )
        self.fogger_toggle.pack(side="left", expand=True, fill="x")

        # ---- Sensors ----
        sens_box = ttk.Labelframe(right, text="Sensors (from IO Mega)", padding=10)
        sens_box.pack(fill="both", expand=True)

        for k, v in self.sensor_vars.items():
            row = ttk.Frame(sens_box, padding=6)
            row.pack(fill="x")
            ttk.Label(row, text=k, font=("Arial", 14)).pack(side="left")
            ttk.Label(row, textvariable=v, font=("Arial", 14, "bold")).pack(side="right")

    def _make_toggle(self, parent, label: str, var: tk.BooleanVar, on_change):
        frm = ttk.Frame(parent, padding=6)

        ttk.Label(frm, text=label, font=("Arial", 14)).pack(side="left")

        btn = ttk.Button(frm, text="OFF")

        def refresh():
            btn.config(text=("ON" if var.get() else "OFF"))

        def toggle():
            var.set(not var.get())
            refresh()
            on_change()

        frm._toggle_btn = btn
        frm._toggle_refresh = refresh

        btn.config(command=toggle)
        btn.pack(side="right")
        refresh()
        return frm

    # ================= Presets =================
    def _open_save_preset_popup(self):
        win = tk.Toplevel(self)
        win.title("Save Preset")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Save current settings into which preset?",
            padding=10,
            font=("Arial", 12)
        ).pack(fill="x")

        choice = tk.IntVar(value=int(self.selected_preset.get()))

        body = ttk.Frame(win, padding=10)
        body.pack(fill="both", expand=True)

        for i in range(1, 6):
            ttk.Radiobutton(body, text=f"Preset {i}", variable=choice, value=i).pack(anchor="w", pady=2)

        btns = ttk.Frame(win, padding=10)
        btns.pack(fill="x")

        def do_save():
            idx = int(choice.get())
            self._save_preset(idx)
            win.destroy()

        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Save", command=do_save).pack(side="right")

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (win.winfo_width() // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{x}+{y}")

    def _save_preset(self, idx: int):
        fan = [int(v.get()) for v in getattr(self, "fan_vars", [])]
        if len(fan) != 8:
            fan = [0] * 8

        pump = int(getattr(self, "pump_pwm", tk.IntVar(value=0)).get())
        pump = max(0, min(UI_MAX, pump))

        self.presets[idx] = {
            "fans": [max(0, min(UI_MAX, int(x))) for x in fan],
            "pump": pump,
            "heat": bool(self.heat_on.get()),
            "fog": bool(self.fog_on.get()),
            "ts": time.time(),
        }

        self._persist_presets()
        self.status_io.set(f"IO: preset {idx} saved")

    def _persist_presets(self):
        try:
            folder = os.path.dirname(PRESETS_PATH)
            if folder:
                os.makedirs(folder, exist_ok=True)

            data = {
                "version": 1,
                "saved_at": time.time(),
                "ui_max": UI_MAX,
                "fan_min_pwm": FAN_MIN_PWM,
                "fan_max_pwm": FAN_MAX_PWM,
                "presets": self.presets,
            }

            tmp = PRESETS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            os.replace(tmp, PRESETS_PATH)
        except Exception as e:
            self.status_io.set(f"IO: preset save ERROR | {e}")

    def _load_presets(self):
        try:
            if not os.path.exists(PRESETS_PATH):
                return

            with open(PRESETS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            presets = data.get("presets", {})

            # Compatibility: if old file stored 0..255 UI values, normalize to 0..100
            def norm_ui(v):
                try:
                    v = int(v)
                except Exception:
                    return 0
                if v > UI_MAX:
                    # assume old UI was 0..255
                    return int(round(v * UI_MAX / 255))
                return max(0, min(UI_MAX, v))

            for i in range(1, 6):
                p = presets.get(str(i), presets.get(i))
                if isinstance(p, dict) and "fans" in p and "pump" in p and "heat" in p and "fog" in p:
                    fans = p.get("fans", [0] * 8)
                    if not isinstance(fans, list) or len(fans) != 8:
                        fans = [0] * 8

                    self.presets[i] = {
                        "fans": [norm_ui(x) for x in fans],
                        "pump": norm_ui(p.get("pump", 0)),
                        "heat": bool(p.get("heat", False)),
                        "fog": bool(p.get("fog", False)),
                        "ts": p.get("ts", time.time()),
                    }
        except Exception:
            self.presets = {i: None for i in range(1, 6)}

    def _apply_selected_preset(self):
        idx = int(self.selected_preset.get())
        self._apply_preset(idx)

    def _apply_preset(self, idx: int):
        p = self.presets.get(idx)
        if not p:
            self.status_io.set(f"IO: preset {idx} is empty")
            return

        self._suspend_ui_callbacks = True
        try:
            for i in range(8):
                v = max(0, min(UI_MAX, int(p["fans"][i])))
                self.fan_vars[i].set(v)
                self.fan_scales[i].set(v)
                self.fan_val_labels[i].config(text=str(v))

            pv = max(0, min(UI_MAX, int(p["pump"])))
            self.pump_pwm.set(pv)
            self.pump_scale.set(pv)
            self.pump_val_label.config(text=str(pv))

            self.heat_on.set(bool(p["heat"]))
            self.fog_on.set(bool(p["fog"]))
            if hasattr(self.heater_toggle, "_toggle_refresh"):
                self.heater_toggle._toggle_refresh()
            if hasattr(self.fogger_toggle, "_toggle_refresh"):
                self.fogger_toggle._toggle_refresh()
        finally:
            self._suspend_ui_callbacks = False

        self._send_fan_and_pump_state()
        self._send_io_cmd("HEAT", self.heat_on.get())
        self._send_io_cmd("FOG", self.fog_on.get())
        self.status_io.set(f"IO: preset {idx} applied")

    def _all_off(self):
        self._suspend_ui_callbacks = True
        try:
            for i in range(8):
                self.fan_vars[i].set(0)
                self.fan_scales[i].set(0)
                self.fan_val_labels[i].config(text="0")

            self.pump_pwm.set(0)
            self.pump_scale.set(0)
            self.pump_val_label.config(text="0")

            self.heat_on.set(False)
            self.fog_on.set(False)
            if hasattr(self.heater_toggle, "_toggle_refresh"):
                self.heater_toggle._toggle_refresh()
            if hasattr(self.fogger_toggle, "_toggle_refresh"):
                self.fogger_toggle._toggle_refresh()
        finally:
            self._suspend_ui_callbacks = False

        self._send_fan_and_pump_state()
        self._send_io_cmd("HEAT", False)
        self._send_io_cmd("FOG", False)
        self.status_io.set("IO: ALL OFF sent")

    # ================= PWM Mapping =================
    def _map_fan_pwm(self, ui_val: int) -> int:
        # UI: 0..100  -> HW: 0 or FAN_MIN_PWM..FAN_MAX_PWM
        if ui_val <= 0:
            return 0
        ui_val = max(0, min(UI_MAX, ui_val))
        return int(FAN_MIN_PWM + (ui_val / UI_MAX) * (FAN_MAX_PWM - FAN_MIN_PWM))

    def _map_pump_pwm(self, ui_val: int) -> int:
        # UI: 0..100 -> HW: 0..255
        ui_val = max(0, min(UI_MAX, ui_val))
        return int((ui_val / UI_MAX) * 255)

    # ================= FAN handlers =================
    def _send_fan_and_pump_state(self):
        # fan vars are UI 0..100
        vals_ui = [int(v.get()) for v in self.fan_vars]
        vals = [self._map_fan_pwm(x) for x in vals_ui]

        p_ui = int(getattr(self, "pump_pwm", tk.IntVar(value=0)).get())
        p_ui = max(0, min(UI_MAX, p_ui))
        p = self._map_pump_pwm(p_ui)

        cmd = " ".join([f"F{i+1}={vals[i]}" for i in range(8)]) + f" P1={p}"

        ok = self.fan_link.write_line(cmd)
        if ok:
            self.status_fan.set(f"FAN: connected ({FAN_PORT}) | last: {cmd}")
        else:
            err = self.fan_link.last_err or "not connected"
            self.status_fan.set(f"FAN: ERROR ({FAN_PORT}) | {err}")

    # ================= IO handlers =================
    def _send_io_cmd(self, key: str, on: bool):
        cmd = f"IO;{key}={1 if on else 0}"
        ok = self.io_link.write_line(cmd)
        if ok:
            self.status_io.set(f"IO: connected ({IO_PORT}) | last: {cmd}")
        else:
            err = self.io_link.last_err or "not connected"
            self.status_io.set(f"IO: ERROR ({IO_PORT}) | {err}")

    # ================= Parsing =================
    def _parse_sens(self, line: str):
        parts = line.split(";")
        kv = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.strip()] = v.strip()

        # DS18B20 T1..T4 -> Temp 1..4
        for i in range(1, 5):
            key = f"T{i}"
            label = f"Temp {i} (°C)"
            if key in kv and label in self.sensor_vars:
                self.sensor_vars[label].set(kv[key])

        # TAMB -> Temp 5
        if "TAMB" in kv and "Temp 5 (°C)" in self.sensor_vars:
            self.sensor_vars["Temp 5 (°C)"].set(kv["TAMB"])

        if "H" in kv and "Humidity (%)" in self.sensor_vars:
            self.sensor_vars["Humidity (%)"].set(kv["H"])

        if "DUSTMG" in kv and "Dust (mg/m³)" in self.sensor_vars:
            self.sensor_vars["Dust (mg/m³)"].set(kv["DUSTMG"])

    # ================= Polling =================
    def _poll_serial(self):
        if self.fan_link.is_open():
            pass
        elif self.fan_link.last_err:
            self.status_fan.set(f"FAN: ERROR ({FAN_PORT}) | {self.fan_link.last_err}")
        else:
            self.status_fan.set(f"FAN: not connected ({FAN_PORT})")

        if self.io_link.is_open():
            pass
        elif self.io_link.last_err:
            self.status_io.set(f"IO: ERROR ({IO_PORT}) | {self.io_link.last_err}")
        else:
            self.status_io.set(f"IO: not connected ({IO_PORT})")

        for line in self.io_link.read_lines(max_lines=30):
            if line.startswith("SENS;"):
                self._parse_sens(line)

        self.after(200, self._poll_serial)


if __name__ == "__main__":
    App().mainloop()
