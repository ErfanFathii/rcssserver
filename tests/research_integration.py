#!/usr/bin/env python3
"""Live UDP acceptance test; no third-party Python dependencies.

Usage: python3 tests/research_integration.py build/rcssserver
"""
import collections
import json
import math
from pathlib import Path
import random
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zlib


def free_ports():
    for _ in range(100):
        base = random.randrange(20000, 55000)
        sockets = []
        try:
            for port in range(base, base + 3):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sockets.append(s)
                s.bind(('127.0.0.1', port))
            return base
        except OSError:
            pass
        finally:
            for s in sockets:
                s.close()
    raise RuntimeError('No free port block')


class Client:
    def __init__(self, port, team, version):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self.sock.settimeout(3)
        self.sock.sendto(f'(init {team} (version {version}))\0'.encode(), ('127.0.0.1', port))
        data, self.addr = self.sock.recvfrom(65535)
        assert data.startswith(b'(init '), data
        init = data.decode().split()
        self.key = (init[1], int(init[2]))
        self.messages = [data.decode('latin1')]
        self.decoder = None
        self.encoder = None

    def send(self, msg):
        data = msg.encode() + b'\0'
        if self.encoder:
            encoder = zlib.compressobj(6)
            data = encoder.compress(data) + encoder.flush(zlib.Z_SYNC_FLUSH)
        self.sock.sendto(data, self.addr)

    def receive(self):
        data = self.sock.recv(65535)
        if self.decoder:
            data = zlib.decompressobj().decompress(data)
        message = data.decode('latin1')
        self.messages.append(message)
        if message.startswith('(ok compression 6)'):
            self.decoder = zlib.decompressobj()
            self.encoder = zlib.compressobj(6)
        return message


def collect(clients, seconds, actions=False, synch=False):
    deadline = time.monotonic() + seconds
    sockets = {c.sock: c for c in clients}
    acted = set()
    while time.monotonic() < deadline:
        ready, _, _ = select.select(list(sockets), [], [], 0.05)
        for sock in ready:
            c = sockets[sock]
            message = c.receive()
            if (actions and message.startswith('(sense_body')
                    and int(message.split()[1]) >= 3 and c.key not in acted):
                # Only the first primary action is accepted in this cycle.
                c.send('(dash 40)(dash 80)(turn_neck 10)')
                acted.add(c.key)
            if synch and message.startswith('(think)'):
                c.send('(done)')


def run(binary, mode, directory, synch=False):
    port = free_ports()
    log = directory / 'match.json'
    console = directory / 'server.log'
    args = [binary, f'server::port={port}', f'server::coach_port={port+1}',
            f'server::olcoach_port={port+2}', f'server::observation_mode={mode}',
            f'server::json_log_file={log}', 'server::text_logging=false',
            'server::game_logging=false', 'server::coach=true', 'server::fullstate_l=false',
            'server::fullstate_r=false', 'server::auto_mode=false',
            'server::synch_mode=' + str(synch).lower()]
    clients = []
    with console.open('w') as output:
        server = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, cwd=directory)
        try:
            deadline = time.monotonic() + 10
            while 'Research JSON log:' not in console.read_text():
                assert server.poll() is None, console.read_text()
                assert time.monotonic() < deadline, 'Server startup timeout'
                time.sleep(0.05)
            for team in ('ResearchL', 'ResearchR'):
                for i in range(11):
                    c = Client(port, team, 16 if i == 0 else 18)
                    clients.append(c)
                    c.send(f'(move {-45 + i * 3} {(i % 3 - 1) * 10})')
                    c.send(f'(change_view {("wide", "normal", "narrow")[i % 3]} high)')
            # Exercise compression and a legacy low-quality view request.
            clients[2].send('(compression 6)')
            clients[0].send('(change_view wide low)')
            clients[1].send('(gaussian_see)')
            collect(clients, 1.5, synch=synch)
            coach = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            coach.settimeout(3)
            coach.sendto(b'(init (version 18))\0', ('127.0.0.1', port + 1))
            data, addr = coach.recvfrom(65535)
            assert data.startswith(b'(init'), data
            coach.sendto(b'(change_mode play_on)\0', addr)
            collect(clients, 2, actions=True, synch=synch)
            coach.close()
        finally:
            server.send_signal(signal.SIGINT)
            server.wait(timeout=10)
            for c in clients:
                c.sock.close()
    assert server.returncode == 0, console.read_text()
    document = json.loads(log.read_text())
    events = document['events']
    assert document['observation_mode'] == mode
    assert [e['seq'] for e in events] == list(range(1, len(events)+1))
    states = {e['seq']: e for e in events if e['kind'] == 'state'}
    observations = [e for e in events if e['kind'] == 'observation']
    seen = collections.defaultdict(list)
    for e in observations:
        assert e['state_id'] < e['seq']
        state = states[e['state_id']]
        assert (state['time'], state['stoppage_time']) == (e['time'], e['stoppage_time'])
        assert len(state['players']) == 22
        seen[e['side'], e['unum']].append(e)
    # Every datagram actually received is present byte-for-byte, including NULs.
    for c in clients:
        recorded = collections.Counter(e['message'] for e in seen[c.key])
        received = collections.Counter(c.messages)
        assert not (received - recorded), (c.key, list((received - recorded).items())[:2])
    assert clients[2].decoder is not None, 'Compressed transport was not exercised'
    assert not any(e['message'].startswith('(fullstate ') for e in observations)
    results = [e for e in events if e['kind'] == 'command_result']
    # Dash effects/counters are committed at the next simulation step in v19.
    # The pending leg command proves the first power won over the duplicate.
    assert all(p['action_counts']['dash'] == 1 for p in list(states.values())[-1]['players'])
    pending = [p for e in states.values() for p in e['players'] if p['legs']['left']['command'] == 'dash']
    assert pending and all(p['legs']['left']['dash_power'] == 40 for p in pending)
    assert any(e['counts_after']['turn_neck'] - e['counts_before']['turn_neck'] == 1 for e in results)
    assert any(any(p['velocity'] != [0, 0] for p in e['players']) for e in states.values())
    all_see = []
    for c in clients:
        frames = [e for e in seen[c.key] if e['message'].startswith('(see ')
                  and e['time'] >= 3]
        assert len(frames) >= 3, (mode, c.key, len(frames))
        all_see.extend(frames)
        if mode == 'all_players_visible':
            keys = [(e['time'], e['stoppage_time']) for e in frames]
            assert len(keys) == len(set(keys)), 'Duplicate see in one simulation cycle'
            times = [e['time'] for e in frames]
            assert all(b == a + 1 for a, b in zip(times, times[1:])), times
            for e in frames:
                # Every active player must have an explicit team and uniform number.
                count = len(re.findall(r'\(\([pP](?:\s|\))', e['message']))
                assert count == 21, (c.key, count, e['message'])
                labelled = re.findall(r'\(\(p "[^"]+" \d+(?: goalie)?\)', e['message'])
                assert len(labelled) == 21, (c.key, e['message'])
                assert re.search(r'\(\(b\) [\d.e+-]+ -?[\d.e+-]+', e['message'])
        elif c.key[1] == 2:  # v18 normal: every second cycle
            times = [e['time'] for e in frames]
            assert all(b - a == 2 for a, b in zip(times, times[1:])), times
    if mode == 'all_players_visible':
        assert any('(ok gaussian_see)' in e['message'] for e in observations)
        assert not any('gaussian_see_unsupported' in e['message'] for e in observations)
        # Validate ball quantization from exact state, not a separately rounded fullstate.
        nonzero_error = 0
        behind = 0
        gaussian_frames = 0
        for e in all_see:
            state = states[e['state_id']]
            p = next(p for p in state['players'] if (p['side'], p['unum']) == (e['side'], e['unum']))
            ball = state['ball']['position']
            distance = math.dist(ball, p['position'])
            expected = round(math.exp(round(math.log(distance + 1e-10) / .1) * .1) / .1) * .1
            match = re.search(r'\(\(b\) ([\d.e+-]+) (-?[\d.e+-]+)', e['message'])
            actual, angle = map(float, match.groups())
            if p['gaussian_see']:
                gaussian_frames += 1
                assert math.isfinite(actual) and actual >= 0
            else:
                assert abs(actual - expected) < 1e-6, (actual, expected, distance)
            nonzero_error += abs(actual-distance) > .001
            behind += abs(angle) > 90
        assert nonzero_error and behind and gaussian_frames
    else:
        assert any('(ok gaussian_see)' in e['message'] for e in observations)
        assert any(len(re.findall(r'\(\([pP](?:\s|\))', e['message'])) < 21 for e in all_see)
    print(f'PASS {mode} synch={synch}: {len(all_see)} see frames; '
          f'{sum(len(c.messages) for c in clients)} exact messages; {len(states)} states', flush=True)
    return args


def main():
    binary = str(Path(sys.argv[1] if len(sys.argv) > 1 else 'build/rcssserver').resolve())
    with tempfile.TemporaryDirectory(prefix='rcss-research-test-') as tmp:
        root = Path(tmp)
        for mode, synch in [('standard', False), ('all_players_visible', False), ('all_players_visible', True)]:
            directory = root / (mode + str(synch))
            directory.mkdir()
            args = run(binary, mode, directory, synch)
        rejected = subprocess.run(args, capture_output=True, timeout=10)
        assert b'file already exists' in rejected.stderr
        rejected = subprocess.run([binary, 'server::observation_mode=invalid'], capture_output=True, timeout=10)
        assert b'Invalid observation_mode' in rejected.stderr
        print('PASS invalid mode and existing-log rejection')


if __name__ == '__main__':
    main()
