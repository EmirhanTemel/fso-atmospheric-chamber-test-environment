# Free-Space Optical (FSO) Channel Environmental Control System

## Overview

This project presents an embedded control system developed for environmental simulation in a Free-Space Optical (FSO) communication test platform.

The system integrates a Raspberry Pi-based touchscreen interface with an Arduino Mega 2560 microcontroller to provide real-time environmental monitoring and multi-channel actuator control.

The architecture follows a distributed control model:

- Raspberry Pi → Supervisory UI & control logic
- Arduino Mega → Real-time hardware IO handling
- USB Serial Communication → 9600 baud

---

## Hardware Architecture

### Fan & Pump Control

- Arduino Mega 2560
- L298N motor drivers
- PWM-based speed control
- 12V power supply for actuators
- Common ground across all modules

![Fan Driver Schematic](./hardware/schematics/Schematic_arduinoFANS.png)

---

## Sensor & IO Interface

Environmental monitoring components:

- DHT22 – Humidity sensor
- DS18B20 – Temperature sensors (OneWire, 4.7kΩ pull-up)
- GP2Y1010AU0F – Dust sensor (220µF capacitor + 150Ω resistor)
- 5V Opto-isolated 2-channel relay module
- 220VAC heater switching via COM–NO configuration

![IO & Sensor Schematic](./hardware/schematics/Schematic_arduinoIO.png)

---

## Functional Capabilities

The system enables:

- Real-time temperature, humidity, and dust monitoring
- Manual multi-channel fan speed adjustment
- Pump speed control via PWM
- Heater ON/OFF switching
- Touchscreen-based supervisory control
- Serial-based actuator command protocol

---

## Communication Layer

- USB Serial Communication
- Baud Rate: 9600
- Command-based actuator control
- Periodic sensor data transmission to Raspberry Pi

---

## Software Architecture

### Raspberry Pi (Python)

- Touchscreen UI implementation
- Serial communication interface
- Manual supervisory control logic
- Real-time sensor visualization

### Arduino (C/C++)

- PWM signal generation
- Sensor data acquisition
- Serial command parsing
- Relay switching control

---

## Engineering Highlights

- Multi-device embedded system integration
- Mixed-voltage design (5V / 12V / 220VAC)
- Opto-isolated relay switching
- Real-time hardware control
- Practical serial protocol implementation
- System-level debugging and hardware validation
