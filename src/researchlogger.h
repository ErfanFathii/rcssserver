// Research telemetry for rcssserver. Licensed under LGPL-3.0-or-later.
#ifndef RCSS_RESEARCHLOGGER_H
#define RCSS_RESEARCHLOGGER_H

#include <cstdint>
#include <fstream>
#include <string>
class Stadium;
class Player;

class ResearchLogger {
    std::ofstream M_out;
    std::uint64_t M_sequence = 0;
    std::uint64_t M_state_id = 0;
    std::string M_last_state;
    void event( const Stadium &, const std::string & fields );
public:
    static ResearchLogger & instance();
    bool open( const Stadium & );
    void close();
    bool enabled() const { return M_out.is_open(); }
    void state( const Stadium & );
    void sent( const Stadium &, const Player &, const std::string & );
    void received( const Stadium &, const Player &, const std::string & );
    void commandResult( const Stadium &, const Player &, const std::string & before );
    static std::string counters( const Player & );
};
#endif
