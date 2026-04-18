#include <Arduino.h>
#include <Servo.h>

#include <cstring>

#include "default_config.hpp"
#include "pointer_protocol.hpp"

namespace {

using namespace targetpointer;

Servo g_pointer_servo;
char g_line_buffer[targetpointer::config::k_serial_line_buffer_size]{};
std::size_t g_line_length = 0;
std::int16_t g_current_angle = targetpointer::config::k_servo_center_angle_deg;
std::int16_t g_target_angle = targetpointer::config::k_servo_center_angle_deg;
char g_last_command[32] = "BOOT";
char g_last_result[32] = "OK:IDLE";
bool g_tracking_led_on = false;
bool g_servo_attached = false;
unsigned long g_last_servo_step_ms = 0;

constexpr std::int16_t k_servo_step_deg = 2;
constexpr unsigned long k_servo_step_interval_ms = 20;

void write_tracking_led(bool on) {
    g_tracking_led_on = on;
    digitalWrite(
        targetpointer::config::k_status_led_pin,
        on ? LOW : HIGH
    );
}

void move_servo(std::int16_t angle_deg) {
    if (!g_servo_attached) {
        g_pointer_servo.attach(targetpointer::config::k_servo_signal_pin);
        g_servo_attached = true;
        g_last_servo_step_ms = millis();
    }
    const std::int16_t safe_angle = clamp_angle(
        angle_deg,
        targetpointer::config::k_servo_min_angle_deg,
        targetpointer::config::k_servo_max_angle_deg
    );
    g_pointer_servo.write(safe_angle);
    g_current_angle = safe_angle;
}

void set_servo_target(std::int16_t angle_deg) {
    g_target_angle = clamp_angle(
        angle_deg,
        targetpointer::config::k_servo_min_angle_deg,
        targetpointer::config::k_servo_max_angle_deg
    );
}

void update_servo_motion() {
    const unsigned long now = millis();
    if (now - g_last_servo_step_ms < k_servo_step_interval_ms) {
        return;
    }
    g_last_servo_step_ms = now;

    if (g_current_angle == g_target_angle) {
        return;
    }

    std::int16_t next_angle = g_current_angle;
    if (g_target_angle > g_current_angle) {
        next_angle = static_cast<std::int16_t>(g_current_angle + k_servo_step_deg);
        if (next_angle > g_target_angle) {
            next_angle = g_target_angle;
        }
    } else {
        next_angle = static_cast<std::int16_t>(g_current_angle - k_servo_step_deg);
        if (next_angle < g_target_angle) {
            next_angle = g_target_angle;
        }
    }
    move_servo(next_angle);
}

void print_ok_angle(std::int16_t angle_deg) {
    Serial.print("OK:ANGLE:");
    Serial.println(angle_deg);
}

void remember_state(const char* command_name, const char* result_text) {
    std::strncpy(g_last_command, command_name, sizeof(g_last_command) - 1);
    g_last_command[sizeof(g_last_command) - 1] = '\0';
    std::strncpy(g_last_result, result_text, sizeof(g_last_result) - 1);
    g_last_result[sizeof(g_last_result) - 1] = '\0';
}

void print_status() {
    Serial.print("STATUS:ANGLE=");
    Serial.print(g_current_angle);
    Serial.print(",TARGET=");
    Serial.print(g_target_angle);
    Serial.print(",ATTACHED=");
    Serial.print(g_servo_attached ? "1" : "0");
    Serial.print(",LED=");
    Serial.print(g_tracking_led_on ? "ON" : "OFF");
    Serial.print(",LAST=");
    Serial.print(g_last_command);
    Serial.print(",RESULT=");
    Serial.println(g_last_result);
}

void handle_command(const Command& command) {
    switch (command.type) {
        case CommandType::Ping:
            remember_state("PING", "PONG");
            Serial.println("PONG");
            return;
        case CommandType::Center:
            set_servo_target(targetpointer::config::k_servo_center_angle_deg);
            remember_state("CENTER", "OK:CENTER");
            Serial.println("OK:CENTER");
            return;
        case CommandType::Stop:
            set_servo_target(g_current_angle);
            remember_state("STOP", "OK:STOP");
            Serial.println("OK:STOP");
            return;
        case CommandType::Angle:
            if (!is_angle_in_safe_range(
                    command.angle_deg,
                    targetpointer::config::k_servo_min_angle_deg,
                    targetpointer::config::k_servo_max_angle_deg)) {
                remember_state("ANGLE", "ERR:BAD_ANGLE");
                Serial.println("ERR:BAD_ANGLE");
                return;
            }
            set_servo_target(command.angle_deg);
            remember_state("ANGLE", "OK:ANGLE");
            print_ok_angle(g_target_angle);
            return;
        case CommandType::LedOn:
            write_tracking_led(true);
            remember_state("LED", "OK:LED:ON");
            Serial.println("OK:LED:ON");
            return;
        case CommandType::LedOff:
            write_tracking_led(false);
            remember_state("LED", "OK:LED:OFF");
            Serial.println("OK:LED:OFF");
            return;
        case CommandType::StatusQuery:
            print_status();
            return;
        case CommandType::Invalid:
        default:
            remember_state("INVALID", "ERR:BAD_CMD");
            Serial.println("ERR:BAD_CMD");
            return;
    }
}

void process_serial_line(char* line) {
    const Command command = parse_command_line(line);
    handle_command(command);
}

void consume_serial_input() {
    while (Serial.available() > 0) {
        const char ch = static_cast<char>(Serial.read());
        if (ch == '\r') {
            continue;
        }

        if (ch == '\n') {
            g_line_buffer[g_line_length] = '\0';
            process_serial_line(g_line_buffer);
            g_line_length = 0;
            g_line_buffer[0] = '\0';
            continue;
        }

        if (g_line_length + 1 >= sizeof(g_line_buffer)) {
            g_line_length = 0;
            g_line_buffer[0] = '\0';
            Serial.println("ERR:LINE_TOO_LONG");
            continue;
        }

        g_line_buffer[g_line_length++] = ch;
    }
}

}  // namespace

void setup() {
    pinMode(targetpointer::config::k_status_led_pin, OUTPUT);
    write_tracking_led(false);

    Serial.begin(targetpointer::config::k_serial_baud);

    delay(targetpointer::config::k_boot_delay_ms);
    g_target_angle = targetpointer::config::k_servo_center_angle_deg;
    g_last_servo_step_ms = millis();

    Serial.println("BOOT");
    Serial.println("OK:IDLE");
}

void loop() {
    consume_serial_input();
    update_servo_motion();
}
