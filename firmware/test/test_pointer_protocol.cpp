#include <cassert>

#include "pointer_protocol.hpp"

using namespace targetpointer;

namespace {

void test_ping_command() {
    const Command command = parse_command_line("PING");
    assert(command.type == CommandType::Ping);
}

void test_center_command_with_whitespace() {
    const Command command = parse_command_line("  CENTER \r\n");
    assert(command.type == CommandType::Center);
}

void test_valid_angle_command() {
    const Command command = parse_command_line("ANGLE:120");
    assert(command.type == CommandType::Angle);
    assert(command.angle_deg == 120);
}

void test_invalid_angle_payload() {
    const Command command = parse_command_line("ANGLE:right");
    assert(command.type == CommandType::Invalid);
}

void test_status_query() {
    const Command command = parse_command_line("STATUS?");
    assert(command.type == CommandType::StatusQuery);
}

void test_unknown_command_is_invalid() {
    const Command command = parse_command_line("TRACK:person");
    assert(command.type == CommandType::Invalid);
}

void test_safe_angle_range() {
    assert(is_angle_in_safe_range(20, 20, 160));
    assert(is_angle_in_safe_range(160, 20, 160));
    assert(!is_angle_in_safe_range(10, 20, 160));
    assert(clamp_angle(10, 20, 160) == 20);
    assert(clamp_angle(180, 20, 160) == 160);
}

}  // namespace

int main() {
    test_ping_command();
    test_center_command_with_whitespace();
    test_valid_angle_command();
    test_invalid_angle_payload();
    test_status_query();
    test_unknown_command_is_invalid();
    test_safe_angle_range();
    return 0;
}
