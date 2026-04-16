#include <Arduino.h>
#include <Servo.h>

#include "default_config.hpp"
#include "pointer_protocol.hpp"

namespace {

using namespace voicepointer;

Servo g_pointer_servo;
char g_line_buffer[voicepointer::config::k_serial_line_buffer_size]{};
std::size_t g_line_length = 0;
std::int16_t g_current_angle = voicepointer::config::k_servo_center_angle_deg;

void write_status_led(bool on) {
    digitalWrite(
        voicepointer::config::k_status_led_pin,
        on ? LOW : HIGH
    );
}

void move_servo(std::int16_t angle_deg) {
    const std::int16_t safe_angle = clamp_angle(
        angle_deg,
        voicepointer::config::k_servo_min_angle_deg,
        voicepointer::config::k_servo_max_angle_deg
    );
    g_pointer_servo.write(safe_angle);
    g_current_angle = safe_angle;
}

void print_ok_angle(std::int16_t angle_deg) {
    Serial.print("OK:ANGLE:");
    Serial.println(angle_deg);
}

void handle_command(const Command& command) {
    switch (command.type) {
        case CommandType::Ping:
            Serial.println("PONG");
            return;
        case CommandType::Center:
            move_servo(voicepointer::config::k_servo_center_angle_deg);
            Serial.println("OK:CENTER");
            return;
        case CommandType::Stop:
            Serial.println("OK:STOP");
            return;
        case CommandType::Angle:
            if (!is_angle_in_safe_range(
                    command.angle_deg,
                    voicepointer::config::k_servo_min_angle_deg,
                    voicepointer::config::k_servo_max_angle_deg)) {
                Serial.println("ERR:BAD_ANGLE");
                return;
            }
            move_servo(command.angle_deg);
            print_ok_angle(g_current_angle);
            return;
        case CommandType::Target:
            Serial.print("OK:TARGET:");
            Serial.println(command.target_name.data());
            return;
        case CommandType::StatusQuery:
            Serial.print("STATUS:ANGLE:");
            Serial.println(g_current_angle);
            return;
        case CommandType::Invalid:
        default:
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
    pinMode(voicepointer::config::k_status_led_pin, OUTPUT);
    write_status_led(false);

    Serial.begin(voicepointer::config::k_serial_baud);
    g_pointer_servo.attach(voicepointer::config::k_servo_signal_pin);

    delay(voicepointer::config::k_boot_delay_ms);
    move_servo(voicepointer::config::k_servo_center_angle_deg);

    Serial.println("BOOT");
    Serial.println("OK:CENTER");
}

void loop() {
    consume_serial_input();
}
