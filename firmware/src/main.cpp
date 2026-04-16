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
char g_last_command[32] = "BOOT";
char g_last_result[32] = "OK:CENTER";

void write_status_led(bool on) {
    digitalWrite(
        targetpointer::config::k_status_led_pin,
        on ? LOW : HIGH
    );
}

void move_servo(std::int16_t angle_deg) {
    const std::int16_t safe_angle = clamp_angle(
        angle_deg,
        targetpointer::config::k_servo_min_angle_deg,
        targetpointer::config::k_servo_max_angle_deg
    );
    g_pointer_servo.write(safe_angle);
    g_current_angle = safe_angle;
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
            move_servo(targetpointer::config::k_servo_center_angle_deg);
            remember_state("CENTER", "OK:CENTER");
            Serial.println("OK:CENTER");
            return;
        case CommandType::Stop:
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
            move_servo(command.angle_deg);
            remember_state("ANGLE", "OK:ANGLE");
            print_ok_angle(g_current_angle);
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
            write_status_led(false);
            continue;
        }

        if (g_line_length + 1 >= sizeof(g_line_buffer)) {
            g_line_length = 0;
            g_line_buffer[0] = '\0';
            Serial.println("ERR:LINE_TOO_LONG");
            continue;
        }

        g_line_buffer[g_line_length++] = ch;
        write_status_led(true);
    }
}

}  // namespace

void setup() {
    pinMode(targetpointer::config::k_status_led_pin, OUTPUT);
    write_status_led(false);

    Serial.begin(targetpointer::config::k_serial_baud);
    g_pointer_servo.attach(targetpointer::config::k_servo_signal_pin);

    delay(targetpointer::config::k_boot_delay_ms);
    move_servo(targetpointer::config::k_servo_center_angle_deg);

    Serial.println("BOOT");
    Serial.println("OK:CENTER");
}

void loop() {
    consume_serial_input();
}
