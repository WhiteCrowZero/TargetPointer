#pragma once

#include <array>
#include <cstdint>

namespace voicepointer {

enum class CommandType : std::uint8_t {
    Invalid = 0,
    Ping,
    Center,
    Stop,
    Angle,
    Target,
    StatusQuery,
};

struct Command {
    CommandType type = CommandType::Invalid;
    std::int16_t angle_deg = 0;
    std::array<char, 32> target_name{};
};

Command parse_command_line(const char* line);
bool is_angle_in_safe_range(std::int16_t angle_deg, std::int16_t min_deg, std::int16_t max_deg);
std::int16_t clamp_angle(std::int16_t angle_deg, std::int16_t min_deg, std::int16_t max_deg);

}  // namespace voicepointer
