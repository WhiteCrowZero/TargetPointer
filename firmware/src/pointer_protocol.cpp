#include "pointer_protocol.hpp"

#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <cstring>

namespace targetpointer {
namespace {

const char* trim_left(const char* text) {
    while (*text != '\0' && std::isspace(static_cast<unsigned char>(*text)) != 0) {
        ++text;
    }
    return text;
}

void trim_right_in_place(char* text) {
    std::size_t len = std::strlen(text);
    while (len > 0) {
        const unsigned char ch = static_cast<unsigned char>(text[len - 1]);
        if (std::isspace(ch) == 0) {
            break;
        }
        text[--len] = '\0';
    }
}

bool parse_integer(const char* text, std::int16_t& out_value) {
    if (text == nullptr || *text == '\0') {
        return false;
    }

    char* end_ptr = nullptr;
    errno = 0;
    const long raw_value = std::strtol(text, &end_ptr, 10);
    if (errno != 0 || end_ptr == text || *trim_left(end_ptr) != '\0') {
        return false;
    }

    if (raw_value < -32768 || raw_value > 32767) {
        return false;
    }

    out_value = static_cast<std::int16_t>(raw_value);
    return true;
}

}  // namespace

Command parse_command_line(const char* line) {
    Command command{};
    if (line == nullptr) {
        return command;
    }

    char buffer[64]{};
    std::strncpy(buffer, line, sizeof(buffer) - 1);
    buffer[sizeof(buffer) - 1] = '\0';
    trim_right_in_place(buffer);

    const char* trimmed = trim_left(buffer);
    if (*trimmed == '\0') {
        return command;
    }

    if (std::strcmp(trimmed, "PING") == 0) {
        command.type = CommandType::Ping;
        return command;
    }
    if (std::strcmp(trimmed, "CENTER") == 0) {
        command.type = CommandType::Center;
        return command;
    }
    if (std::strcmp(trimmed, "STOP") == 0) {
        command.type = CommandType::Stop;
        return command;
    }
    if (std::strcmp(trimmed, "STATUS?") == 0 || std::strcmp(trimmed, "STATUS") == 0) {
        command.type = CommandType::StatusQuery;
        return command;
    }

    const char* colon = std::strchr(trimmed, ':');
    if (colon == nullptr) {
        return command;
    }

    const std::size_t prefix_length = static_cast<std::size_t>(colon - trimmed);
    const char* payload = trim_left(colon + 1);

    if (prefix_length == 5 && std::strncmp(trimmed, "ANGLE", 5) == 0) {
        std::int16_t angle_deg = 0;
        if (!parse_integer(payload, angle_deg)) {
            return command;
        }
        command.type = CommandType::Angle;
        command.angle_deg = angle_deg;
        return command;
    }

    if (prefix_length == 3 && std::strncmp(trimmed, "LED", 3) == 0) {
        if (std::strcmp(payload, "ON") == 0) {
            command.type = CommandType::LedOn;
            return command;
        }
        if (std::strcmp(payload, "OFF") == 0) {
            command.type = CommandType::LedOff;
            return command;
        }
    }

    return command;
}

bool is_angle_in_safe_range(std::int16_t angle_deg, std::int16_t min_deg, std::int16_t max_deg) {
    return angle_deg >= min_deg && angle_deg <= max_deg;
}

std::int16_t clamp_angle(std::int16_t angle_deg, std::int16_t min_deg, std::int16_t max_deg) {
    if (angle_deg < min_deg) {
        return min_deg;
    }
    if (angle_deg > max_deg) {
        return max_deg;
    }
    return angle_deg;
}

}  // namespace targetpointer
