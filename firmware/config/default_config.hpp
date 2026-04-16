#pragma once

#include <Arduino.h>

namespace targetpointer::config {

inline constexpr unsigned long k_serial_baud = 115200;
inline constexpr uint8_t k_servo_signal_pin = PA8;
inline constexpr uint8_t k_status_led_pin = PC13;

inline constexpr int k_servo_min_angle_deg = 20;
inline constexpr int k_servo_center_angle_deg = 90;
inline constexpr int k_servo_max_angle_deg = 160;

inline constexpr unsigned long k_boot_delay_ms = 1200;
inline constexpr size_t k_serial_line_buffer_size = 64;

}  // namespace targetpointer::config
