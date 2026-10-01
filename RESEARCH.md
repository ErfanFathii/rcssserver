# Observation research fork (rcssserver 19.0.0)

This branch adds two operating modes and an optional JSON event log. It is based
on upstream tag `rcssserver-19.0.0` (`ce870013`), matching the Cyrus research
baseline. Ordinary clients can connect unchanged; the research Cyrus library adapts its
view memory and Gaussian decoding for this experiment.

## Build and run

Dependencies: a C++17 compiler, CMake, Boost.System, zlib, flex, and bison.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4

# Standard observations, with research logging enabled:
./build/rcssserver server::observation_mode=standard server::json_log_file=logs/standard-001.json

# Every player and the ball visible in every direction, every simulation cycle:
./build/rcssserver server::observation_mode=all_players_visible server::json_log_file=logs/visible-001.json
```

Ready-to-use configurations are in `configs/standard.conf` and
`configs/all_players_visible.conf`. Both enable `match.json` and explicitly turn
off fullstate for both teams. Run in a fresh match directory:

```sh
/path/to/rcssserver/build/rcssserver include=/path/to/rcssserver/configs/all_players_visible.conf
```

`server::json_log_file` is independent of ordinary `.rcg` and `.rcl` logging.
Its default is empty (disabled), and the default observation mode is `standard`.
Parent directories are created automatically; an existing log is rejected to
avoid overwriting a previous match. Use a different path for each run.

## Modes

| Behavior | `standard` | `all_players_visible` |
|---|---|---|
| Player/ball/landmark view | Upstream field of view and range limits | Full 360°, no range cutoff |
| Visual delivery | Upstream schedule and requested view width | One `see` per simulation cycle, including stoppage cycles |
| Measurement noise | Upstream quantization by default | Quantized by default; Gaussian distance noise when requested; angular rounding retained |
| Identity/detail hiding with distance | Upstream behavior | Team and uniform number retained |
| Legacy low-quality view request | Upstream behavior | Detailed, noisy observations still sent |
| Line observations | Upstream behavior | Upstream behavior, for client localization compatibility |

Every **other active player** is included; an agent does not see itself. Player
identities are retained at every distance. This mode is visibility with
noise, not exact fullstate. Bearings remain relative to body plus neck, so
objects behind the observer have bearings near ±180°. Landmark and ball
observations also cover 360°. Lines retain their original directional geometry
and count because clients use those conventions for localization.

The new mode uses the synchronous visual-delivery phase even for legacy clients
that did not request `synch_see`. It works with both wall-clock and synchronous
(`server::synch_mode=true`) server timers. `change_view` is still acknowledged
and reported normally, but cannot reduce the new mode's coverage or cadence.

Both modes accept the per-player `(gaussian_see)` command and acknowledge it with
`(ok gaussian_see)`. Vision coverage and measurement channel are independent.
Clients that do not request Gaussian retain quantized observations. The research
collector sets Gaussian distance rates explicitly and disables focus noise.

The coordinated code is available in these three branches:

- [Server](https://github.com/ErfanFathii/rcssserver/tree/research-denoising-only)
- [Cyrus team and collection guide](https://github.com/ErfanFathii/cyrus-soccer-simulation-team/tree/research-denoising-only/script/research)
- [Research library fork](https://github.com/ErfanFathii/cyrus-soccer-simulation-lib/tree/research-denoising-only)

## JSON format

The log is one JSON object with `schema_version`, `server_version`,
`observation_mode`, `angle_unit`, and an `events` array. Records are streamed and
flushed as they occur. The array is closed on normal game completion, Ctrl-C,
SIGTERM, or SIGHUP. SIGKILL/power loss can leave an incomplete JSON document.

Each event has a strictly increasing `seq`, simulation `time`, and
`stoppage_time`. Use both time fields: many simulation steps share time zero
before kickoff, and time also stops during some play modes.

* `state`: Exact double-precision ball/player position, velocity, acceleration;
  committed body/neck angles; stamina, capacity, effort, recovery; teams, score,
  play mode; player types, enabled/connected/goalie flags, cards, state bitmask,
  tackle/foul timers, view/focus/noise settings, pending leg commands, and cumulative action counters.
  Coordinates are in the server's global field frame, distances in meters,
  velocities in meters/cycle, and angles in radians. `state_flags` uses upstream
  `src/types.h`; `view_width` uses the upstream protocol enum.
* `observation`: `side`, `unum`, `state_id`, and the **actual serialized message**
  handed to the player's UDP transport. This includes `see`, `sense_body`,
  `hear`, initialization/parameter messages, responses, and any other outgoing
  player message. It captures plaintext before compression, after a successful
  transport flush. Successful UDP send does not guarantee network delivery.
* `command`: The exact incoming player command packet before parsing, identified
  by `side` and `unum`, with the pre-command `state_id`.
* `command_result`: Counters before and after processing the preceding command
  from that player, plus the resulting `state_id`. A packet can contain several
  commands. Some counters update immediately; v19 dash counters update when
  the pending leg action is applied at the next simulation step. Use the
  referenced states’ `legs` fields to inspect accepted, normalized dash commands
  (directions are explicitly in degrees) and later state counters to confirm
  execution. An unchanged immediate dash counter does not mean rejection.
  Counter increments do
  not guarantee a successful physical outcome (for example, ball contact).
  Raw command arguments are the requested values, before server clamping/noise.

`state_id` references a preceding `state` event's `seq`. A snapshot is taken at
every simulation step (before transient state flags are cleared), and at each
message/command boundary. Identical consecutive snapshots are deduplicated.
Thus an observation references the state at its own send boundary, even when
commands or trainer changes occur between normal cycle snapshots. Player type
and server parameter descriptions are available in the captured initialization
messages. Disabled roster slots remain in state snapshots but are not visible.

`message` preserves all bytes, including the terminating NUL (`\u0000`); bytes
outside printable ASCII are escaped with `\u00XX`. To reconstruct the original
plaintext packet in Python, use `event['message'].encode('latin1')`.

Example analysis:

```python
import json

with open('logs/visible-001.json') as stream:
    log = json.load(stream)
states = {}
for event in log['events']:
    if event['kind'] == 'state':
        states[event['seq']] = event
    elif event['kind'] == 'observation' and event['message'].startswith('(see '):
        truth = states[event['state_id']]
        print(event['time'], event['stoppage_time'], event['side'], event['unum'],
              truth['ball']['position'], event['message'].rstrip('\0'))
```

This is a detailed research log, so files can be large and logging adds CPU/I/O
cost. Use a streaming JSON parser for full-match datasets. Exact observation
capture consumes no additional random draws; changing visibility itself changes
the number of noise/detail draws, so trajectories across modes are not paired
replays. Game logging does not enable fullstate or expose truth to players.

## Validation

```sh
python3 tests/research_integration.py build/rcssserver
```

The live UDP test uses 22 players, protocol versions 16 and 18, all view widths,
a legacy low-quality request, and compression. It checks both modes, both timer
paths for the new mode, per-cycle visibility of all 21 other players, noisy ball
measurements against exact logged truth, objects behind the observer, accepted
versus ignored duplicate actions, and byte-for-byte message capture. It also
checks shutdown produces valid JSON, state references and stoppage timestamps,
Gaussian command handling, invalid modes, and existing-log rejection. Ports and
output directories are isolated from normal games.

A local Cyrus-vs-Cyrus smoke match also completed 100 game cycles with all 22
players connected, fullstate disabled, valid JSON output, and observed dash,
turn, move, and kick actions. This checks compatibility, not match strength.
