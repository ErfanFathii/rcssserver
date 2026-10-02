// Research telemetry for rcssserver. Licensed under LGPL-3.0-or-later.
#include "researchlogger.h"
#include "stadium.h"
#include "player.h"
#include "team.h"
#include "serverparam.h"
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <locale>
#include <sstream>
#include <stdexcept>

namespace {
std::string quote( const std::string & text )
{
    std::ostringstream out;
    out << '"';
    for ( unsigned char c : text )
    {
        if ( c == '"' || c == '\\' ) out << '\\' << c;
        else if ( c < 0x20 || c >= 0x7f )
            out << "\\u00" << std::hex << std::setw( 2 ) << std::setfill( '0' ) << int( c );
        else out << c;
    }
    out << '"';
    return out.str();
}
std::string identity( const Player & p )
{
    return "\"side\":" + quote( p.side() == LEFT ? "l" : "r" )
        + ",\"unum\":" + std::to_string( p.unum() );
}
void vector( std::ostream & out, const PVector & v )
{
    out << '[' << v.x << ',' << v.y << ']';
}
void leg( std::ostream & out, const Leg & value )
{
    static const char * types[] = { "move", "dash", "turn", "kick", "tackle", "none" };
    out << "{\"command\":" << quote( types[value.commandType()] )
        << ",\"dash_power\":" << value.dashPower()
        << ",\"dash_direction_degrees\":" << value.dashDir() << '}';
}
std::string snapshot( const Stadium & s )
{
    std::ostringstream out;
    out.imbue( std::locale::classic() );
    out << std::setprecision( 17 );
    static const char * modes[] = PLAYMODE_STRINGS;
    out << "\"cycle\":" << s.time() << ",\"stoppage\":" << s.stoppageTime()
        << ",\"play_mode\":" << quote( modes[s.playmode()] )
        << ",\"teams\":[{\"side\":\"l\",\"name\":" << quote( s.teamLeft().name() )
        << ",\"score\":" << s.teamLeft().point()
        << "},{\"side\":\"r\",\"name\":" << quote( s.teamRight().name() )
        << ",\"score\":" << s.teamRight().point() << "}],\"ball\":{\"position\":";
    vector( out, s.ball().pos() );
    out << ",\"velocity\":"; vector( out, s.ball().vel() );
    out << ",\"acceleration\":"; vector( out, s.ball().accel() );
    out << "},\"players\":[";
    bool first = true;
    for ( const Player * p : s.players() )
    {
        if ( ! first ) out << ',';
        first = false;
        out << '{' << identity( *p ) << ",\"enabled\":" << ( p->isEnabled() ? "true" : "false" )
            << ",\"connected\":" << ( p->connected() ? "true" : "false" )
            << ",\"goalie\":" << ( p->isGoalie() ? "true" : "false" )
            << ",\"type\":" << p->playerTypeId() << ",\"position\":";
        vector( out, p->pos() );
        out << ",\"velocity\":"; vector( out, p->vel() );
        out << ",\"acceleration\":"; vector( out, p->accel() );
        out << ",\"body_angle\":" << p->angleBodyCommitted()
            << ",\"neck_angle\":" << p->angleNeckCommitted()
            << ",\"stamina\":" << p->stamina() << ",\"effort\":" << p->effort()
            << ",\"recovery\":" << p->recovery() << ",\"stamina_capacity\":" << p->staminaCapacity()
            << ",\"state_flags\":" << p->state()
            << ",\"tackle_cycles\":" << p->tackleCycles() << ",\"foul_cycles\":" << p->foulCycles()
            << ",\"yellow_card\":" << ( p->hasYellowCard() ? "true" : "false" )
            << ",\"red_card\":" << ( p->hasRedCard() ? "true" : "false" )
            << ",\"view_width\":" << int( p->viewWidth() )
            << ",\"high_quality\":" << ( p->highQuality() ? "true" : "false" )
            << ",\"gaussian_see\":" << ( p->isGaussianSee() ? "true" : "false" )
            << ",\"focus_distance\":" << p->focusDist() << ",\"focus_direction\":" << p->focusDir()
            << ",\"command_done\":" << ( p->commandDone() ? "true" : "false" )
            << ",\"legs\":{\"left\":";
        leg( out, p->leftLeg() );
        out << ",\"right\":"; leg( out, p->rightLeg() );
        out << "},\"action_counts\":" << ResearchLogger::counters( *p ) << '}';
    }
    out << ']';
    return out.str();
}
}

ResearchLogger & ResearchLogger::instance()
{
    static ResearchLogger logger;
    return logger;
}
bool ResearchLogger::open( const Stadium & s )
{
    const std::string & path = ServerParam::instance().jsonLogFile();
    if ( path.empty() ) return true;
    try
    {
        if ( std::filesystem::exists( path ) )
            throw std::runtime_error( "file already exists" );
        const auto parent = std::filesystem::path( path ).parent_path();
        if ( ! parent.empty() ) std::filesystem::create_directories( parent );
        M_out.exceptions( std::ios::failbit | std::ios::badbit );
        M_out.open( path );
        M_out.imbue( std::locale::classic() );
        M_out << "{\"schema_version\":1,\"server_version\":\"19.0.0\",\"observation_mode\":"
              << quote( ServerParam::instance().observationMode() )
              << ",\"random_seed\":" << ServerParam::instance().randomSeed()
              << ",\"angle_unit\":\"radian\",\"events\":[\n";
        state( s );
        std::cout << "Research JSON log: " << path << std::endl;
        return true;
    }
    catch ( const std::exception & e )
    {
        std::cerr << "Cannot open research JSON log " << path << ": " << e.what() << std::endl;
        return false;
    }
}
void ResearchLogger::close()
{
    if ( ! enabled() ) return;
    M_out << "\n]}\n";
    M_out.close();
}
void ResearchLogger::event( const Stadium & s, const std::string & fields )
{
    if ( M_sequence ) M_out << ",\n";
    M_out << "{\"seq\":" << ++M_sequence << ",\"time\":" << s.time()
          << ",\"stoppage_time\":" << s.stoppageTime() << ',' << fields << '}';
    M_out.flush();
}
void ResearchLogger::state( const Stadium & s )
{
    if ( ! enabled() ) return;
    std::string data = snapshot( s );
    if ( data == M_last_state ) return;
    event( s, "\"kind\":\"state\"," + data );
    M_state_id = M_sequence;
    M_last_state = std::move( data );
}
void ResearchLogger::sent( const Stadium & s, const Player & p, const std::string & bytes )
{
    if ( ! enabled() ) return;
    state( s );
    event( s, "\"kind\":\"observation\"," + identity( p )
           + ",\"state_id\":" + std::to_string( M_state_id )
           + ",\"message\":" + quote( bytes ) );
}
void ResearchLogger::received( const Stadium & s, const Player & p, const std::string & bytes )
{
    if ( ! enabled() ) return;
    state( s );
    event( s, "\"kind\":\"command\"," + identity( p )
           + ",\"state_id\":" + std::to_string( M_state_id )
           + ",\"message\":" + quote( bytes ) );
}
std::string ResearchLogger::counters( const Player & p )
{
    std::ostringstream out;
    out << "{\"kick\":" << p.kickCount() << ",\"dash\":" << p.dashCount()
        << ",\"turn\":" << p.turnCount() << ",\"catch\":" << p.catchCount()
        << ",\"move\":" << p.moveCount() << ",\"turn_neck\":" << p.turnNeckCount()
        << ",\"change_focus\":" << p.changeFocusCount() << ",\"change_view\":" << p.changeViewCount()
        << ",\"say\":" << p.sayCount() << ",\"attentionto\":" << p.attentiontoCount()
        << ",\"pointto\":" << p.arm().getCounter()
        << ",\"tackle\":" << p.tackleCount() << '}';
    return out.str();
}
void ResearchLogger::commandResult( const Stadium & s, const Player & p, const std::string & before )
{
    if ( ! enabled() ) return;
    state( s );
    event( s, "\"kind\":\"command_result\"," + identity( p )
           + ",\"state_id\":" + std::to_string( M_state_id )
           + ",\"counts_before\":" + before + ",\"counts_after\":" + counters( p ) );
}
