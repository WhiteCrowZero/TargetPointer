#pragma once

#include <Arduino.h>

namespace targetpointer::config {

inline constexpr unsigned long k_serial_baud = 115200;
inline constexpr uint8_t k_servo_signal_pin = PA8;
inline constexpr uint8_t k_status_led_pin = PC13;
inline constexpr uint8_t k_green_led_pin = PB0;
inline constexpr uint8_t k_red_led_pin = PB1;
inline constexpr uint8_t k_buzzer_pin = PB12;

inline constexpr int k_servo_min_angle_deg = 20;
inline constexpr int k_servo_center_angle_deg = 90;
inline constexpr int k_servo_max_angle_deg = 160;
inline constexpr int k_servo_step_deg = 1;
inline constexpr unsigned long k_servo_step_interval_ms = 24;
inline constexpr unsigned long k_search_green_blink_interval_ms = 600;
inline constexpr unsigned long k_buzzer_beep_ms = 80;
inline constexpr unsigned long k_buzzer_gap_ms = 100;

inline constexpr unsigned long k_boot_delay_ms = 1200;
inline constexpr size_t k_serial_line_buffer_size = 64;

}  // namespace targetpointer::config
