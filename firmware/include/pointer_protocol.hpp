#pragma once

#include <cstdint>

namespace targetpointer {

enum class CommandType : std::uint8_t {
    Invalid = 0,
    Ping,
    Center,
    Stop,
    Angle,
    LedOn,
    LedOff,
    State,
    BuzzerOn,
    BuzzerOff,
    BuzzerBeep,
    StatusQuery,
};

enum class DeviceMode : std::uint8_t {
    Idle = 0,
    Search,
    Lock,
    Lost,
};

struct Command {
    CommandType type = CommandType::Invalid;
    std::int16_t angle_deg = 0;
    DeviceMode mode = DeviceMode::Idle;
};

Command parse_command_line(const char* line);
const char* device_mode_name(DeviceMode mode);
bool is_angle_in_safe_range(std::int16_t angle_deg, std::int16_t min_deg, std::int16_t max_deg);
std::int16_t clamp_angle(std::int16_t angle_deg, std::int16_t min_deg, std::int16_t max_deg);

}  // namespace targetpointer
