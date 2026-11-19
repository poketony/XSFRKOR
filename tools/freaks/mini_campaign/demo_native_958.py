"""Focused native attract-demo input probe; no rendering or emulator control."""

import struct
import csv
import tomllib
from pathlib import Path
import json
import hashlib


from verify_958 import WideMachine
from interaction_958 import InteractionMachine
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_PC, UC_MIPS_REG_RA, UC_MIPS_REG_V0
from unpack_disc import require
from build_paths import WORK
import demo_958 as demo


def fixture():
    import build_958 as build
    import runtime_958 as runtime
    import mini_korean_build as old
    from mini_stages import Stages
    from unpack_contents import wwrom_parts
    import mini_lexicon_source as corpus
    import mini_db_words as db
    from stage_lexicon import eligible_words
    import wwrom
    root = build.ROOT
    elf, rom = wwrom.originals()
    stages = Stages(next(raw for row, raw in wwrom_parts(elf, rom) if row['name'] == 'stage/1p.stg'))
    with (root / 'DBC-HANGUL.tsv').open(encoding='utf-8', newline='') as stream:
        alphabet = [r['hangul'] for r in csv.DictReader(stream, delimiter='\t') if int(r['occurrences'])]
    with (root / 'CampaignWordIds.tsv').open(encoding='utf-8', newline='') as stream:
        ids = {r['word']: int(r['word_id']) for r in csv.DictReader(stream, delimiter='\t')}
    tokens = {c: i for i, c in enumerate(alphabet)}
    candidates = {row['key'] for row in corpus.common_rows() if not row['entry_id'].startswith('DBC:')}
    db_keys = {row['word'] for row in db.read_editor(root / 'XenosagaWords.txt') if db.native_slot(row)}
    candidates.update(w for w in db_keys if 2 <= len(w) <= 9)
    for path in (old.WORDS, root / 'CampaignWords.tsv'):
        with path.open(encoding='utf-8', newline='') as stream:
            candidates.update(corpus.search_key(row['word']) for row in csv.DictReader(stream, delimiter='\t')
                              if row['source_id'].startswith('JPN:') and len(row['word']) >= 2)
    descriptions = [p for p in tomllib.loads((root / 'StagesKorean.toml').read_text(encoding='utf-8'))['stages']
                    if p['index'] in (19, 25)]
    descriptions += [p for p in build.versus_profiles(elf, stages, root / 'VersusKorean.tsv')
                     if p['index'] in (103, 105)]
    profiles, edits = [], {}
    for p in descriptions:
        s = build.apply_goal_rule(stages.stages[p['index']], p)
        used = set(p['preplaced'].replace('?', '') + p['inventory'])
        words = eligible_words({w for w in candidates if set(w) <= used}, used, db_keys, p['lore_words'], p['index'],
                               excluded_words=p.get('word_exclusions', ()))
        goals, aliases = build.compile_goals(s, p.get('goal_targets'), words, tokens)
        basic = {k: p[k] for k in ('index', 'name', 'description', 'hints', 'preplaced', 'inventory', 'route')}
        source = dict.fromkeys(used, 0)
        source['?'] = 0x45
        edits[s.index] = old.stage_bytes(s, basic, source, goal_records=goals)
        preplaced = [(r[1], r[2], tokens[c] if c != '?' else 0xffff)
                     for c, r in zip(p['preplaced'], (r for g in s.board.pieces for r in g))]
        random = None
        if p.get('mode') == 'versus':
            random = [0] * 72
            for t, c in zip(s.inventory, p['inventory']):
                random[t] = tokens[c]
        profiles.append(dict(stage=s.index, preplaced=preplaced, inventory=[tokens[c] for c in p['inventory']],
                             words=[(tuple(tokens[c] for c in w), ids[w]) for w in words],
                             goal_letters=aliases, mode=int(random is not None), random_letters=random))
    stages = old.rebuild_stages(stages, edits)
    elf, rom, _ = wwrom.rebuild(elf, rom, {'stage/1p.stg': stages.data})
    elf, patch = runtime.install(elf, alphabet, profiles, [bytes(16)] * 958)
    return elf, rom, dict(alphabet=alphabet, korean_word_ids=ids, patch=patch, profiles=descriptions,
                         compiled_profiles=profiles)


class DemoMachine(WideMachine):
    def __init__(self, elf, rom, *, dictionary=None):
        super().__init__(elf, rom)
        self.dictionary = dictionary
        self.install_probe_hooks()

    def install_probe_hooks(self):
        self.steps = []
        self.hooks = []
        self.native_events = []
        from demo_audit_958 import audit_title
        self.title_requests = {row['stage']: row for row in audit_title(self.elf.data)['title_requests']}
        for at in (0x12b7e8, 0x12b818):
            self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, InteractionMachine.multiply.__get__(self),
                                               begin=at, end=at))
        for at in (0x120918, 0x12094c, 0x124d0c):
            self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.destination_multiply, begin=at, end=at))
        for at in (0x12b28c, 0x12b294):
            self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.divide, begin=at, end=at))
        for at in (0x1126a8, 0x112fa8, 0x10fa38, 0x10fb30,
                   0x10fb98, 0x10fc28, 0x18a598, 0x10a670,
                   0x1447c8, 0x12df58, 0x14dea0, 0x1289d0, 0x11f790, 0x1155c0,
                   0x1157c8, 0x1294c0, 0x129408, 0x1294a0, 0x12c008, 0x12fa38,
                   0x12fa50, 0x131598, 0x1269e8, 0x1253a8, 0x113810, 0x12a618,
                   0x14b910, 0x14b908, 0x14b8b0, 0x14c020, 0x14c028, 0x137bc0,
                   0x14c008, 0x14bfd8, 0x1306f0, 0x1430a0, 0x131130, 0x131250,
                   0x1434e8, 0x147398):
            self.stub(at)
        self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.memset, begin=0x1a87f8, end=0x1a87f8))
        self.stub(0x128a88, 1800)
        self.stub(0x14c088, 1200)
        if getattr(self, 'dictionary', None) is None:
            self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.word_event, begin=0x1267a0, end=0x1267a0))
        else:
            for at in (0x113b18, 0x113dbc, 0x113dc0, 0x113e28, 0x113e3c,
                       0x113ef4, 0x113ef8, 0x113f54, 0x113f70):
                self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.destination_multiply, begin=at, end=at))
            self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.native_event_return,
                                               begin=0x1269d0, end=0x1269d0))
            for at in (0x1a9ca0, 0x1a9f88, 0x1a9dd8):
                self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.cstring, begin=at, end=at))
        for at in (0x145e90, 0x14993c):
            self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.accepted, begin=at, end=at))

    def accepted(self, uc, at, *_):
        query = uc.reg_read(UC_MIPS_REG_V0)
        if not query:
            return
        lane = 0 if at == 0x145e90 else uc.reg_read(UC_MIPS_REG_0 + 19)
        coords = 0x272bbc if at == 0x145e90 else 0x272dcc + lane * 20
        cursor = self.u32(0x272b9c if at == 0x145e90 else 0x272da8 + lane * 4)
        token = struct.unpack('<H', self.read(self.u32(cursor + 0xa0), 2))[0]
        ids = [self.u32(word) & 0xfffff for i in range(self.u32(query + 0xe000))
               if (word := self.u32(query + i * 56 + 0x28))]
        self.steps.append(dict(lane=lane, token=token, x=self.u32(coords), y=self.u32(coords + 4),
                               frame=self.frame, word_ids=ids))

    def native_event_return(self, uc, *_):
        controller = uc.reg_read(UC_MIPS_REG_0 + 16)
        word_id = self.u32(controller + 0x970)
        require(0 < word_id <= len(self.dictionary.words), 'Demo event selected an absent production word')
        word = self.dictionary.words[word_id - 1]
        require(self.read(controller + 0x97c, len(word.raw)) == word.raw,
                'Demo word event did not load its actual production WDT bytes')
        self.native_events.append(dict(frame=self.frame, word_id=word_id, wdt_hex=word.raw.hex()))

    def cstring(self, uc, at, *_):
        # These libc helpers select unsupported EE SIMD for aligned WDT buffers.
        # Preserve C byte semantics; native 113AD0 still prepares actual glyph IDs.
        destination, source, count = (uc.reg_read(UC_MIPS_REG_0 + r) for r in (4, 5, 6))
        if at == 0x1a9ca0:
            raw = self.read(destination, 256)
            require(b'\0' in raw, 'Demo text exceeds its native 256-byte string bound')
            result = raw.index(0)
        else:
            require(count <= 256, 'Demo libc string copy exceeds its native record bound')
            raw = self.read(source, count).split(b'\0', 1)[0]
            if at == 0x1a9f88:
                uc.mem_write(destination, raw.ljust(count, b'\0'))
            else:
                prefix = self.read(destination, 256)
                require(b'\0' in prefix, 'Demo string append destination is unterminated')
                uc.mem_write(destination + prefix.index(0), raw + b'\0')
            result = destination
        uc.reg_write(UC_MIPS_REG_V0, result)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def memset(self, uc, *_):
        pointer, value, count = (uc.reg_read(UC_MIPS_REG_0 + r) for r in (4, 5, 6))
        require(count <= 1 << 20, 'Demo EE memset exceeds its audited allocation bound')
        uc.mem_write(pointer, bytes((value & 255,)) * count)
        uc.reg_write(UC_MIPS_REG_V0, pointer)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def divide(self, uc, at, *_):
        if at == 0x12b28c:
            x, y = (uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (3, 4))
            x = x if x < 0x80000000 else x - 0x100000000
            self.quotient = int(x / y)
        else:
            uc.reg_write(UC_MIPS_REG_0 + 3, self.quotient & 0xffffffffffffffff)
        uc.reg_write(UC_MIPS_REG_PC, at + 4)

    def stub(self, at, value=0):
        def skip(uc, *_):
            uc.reg_write(UC_MIPS_REG_V0, value)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, skip, begin=at, end=at))

    def put(self, at, *values):
        self.uc.mem_write(at, struct.pack(f"<{len(values)}I", *values))

    def setup(self, stage):
        self.stage, self.frame, self.steps, self.ready_at = stage, 0, [], None
        self.uc.mem_write(0x272b80, bytes(0x210))
        self.uc.mem_write(0x272cf8, bytes(0x98))
        request = self.STACK - 0x4000
        self.uc.mem_write(request, bytes.fromhex(self.title_requests[stage]['request_hex']).ljust(0x90, b'\0'))
        self.call(0x143460, request, count=10_000_000)
        self.board = self.u32(0x272b88)
        base = 0x272b80
        state = self.u32(base)
        require(state == 0x272cb0 and self.u32(state + 12) == self.title_requests[stage]['initial_state'],
                'Solo probe is not using the original title UI state')
        self.put(base + 0xc, self.call(0x122638, self.board, 0))
        self.put(base + 0x18, self.call(0x126a78, self.board + 0x23c0c))
        self.uc.reg_write(UC_MIPS_REG_0 + 8, 0)
        self.put(base + 0x1c, self.call(0x12ae38, 1, 0, 14, 18))
        hud = self.STACK - 0x3000
        self.uc.mem_write(hud, bytes(0x200))
        self.put(hud + 0x34, self.board + 0x24a7c)
        self.put(hud + 0x58, hud + 0x100, hud + 0x100)
        self.put(base + 0x34, hud)
        self.call(0x12c0b8, self.u32(base + 0x34), self.board)
        self.call(0x120970, self.board, base + 0x3c, base + 0x40)
        self.put(base + 0x114, 0)
        self.uc.reg_write(UC_MIPS_REG_0 + 8, 28)
        self.put(base + 0x10, self.call(0x126098, self.board, 0, 0, 29))
        self.put(base + 0x14, self.call(0x124a08))
        self.put(0x273080, 1)
        self.warmup_trace = []
        for _ in range(20):
            self.tick(0)
            self.warmup_trace.append((self.u32(state + 12), self.u32(state + 16)))
        require(self.u32(state + 12) == 5, 'Native solo title setup did not finish during warmup')
        self.frame, self.ready_at = 0, 0

    def word_event(self, uc, *_):
        # The focused fixture omits WDT/font generation. Preserve the native event
        # fields used by goals and the original 60/48-frame query pacing only.
        controller = uc.reg_read(UC_MIPS_REG_0 + 4)
        query = self.u32(controller + 0x96c)
        word = self.u32(query + 0x28)
        self.put(controller + 0x970, self.u32(word) & 0xfffff, self.u32(query + 0x24), 1)
        self.put(controller + 0xa98, 1)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def tick(self, pad):
        self.frame += 1
        self.call(0x143520, pad)
        cursor = self.u32(0x272b9c)
        controller = self.u32(0x272b90)
        self.put(controller + 0xa98, self.u32(controller + 0xa98) + 1)
        self.call(0x12b328, cursor)
        self.call(0x122c10, self.u32(0x272b8c))
        return self.call(0x12b320, cursor)

    def record_solo(self, profile, moves):
        from collections import deque
        from unpack_disc import require
        self.setup(profile['index'])
        frames = []

        def frame(pad=0, count=1):
            for _ in range(count):
                frames.append(pad)
                self.tick(pad)

        def wait_ready():
            for _ in range(1200):
                if (self.call(0x12b320, self.u32(0x272b9c)) == 0
                        and not self.u32(0x272bcc) and self.u32(self.board + 0x232b4) == 1):
                    frame(0, 15)
                    return
                frame()
            raise ValueError(f'Demo {self.stage} did not finish native animation: '
                             f'cursor={self.call(0x12b320, self.u32(0x272b9c))}, '
                             f'motion={self.u32(self.board + 0x232b4)}, query={self.u32(0x272bcc):#x}')

        frame(0, 90)
        for step in profile['route'][:moves]:
            wait_ready()
            frame(0x200)
            frame(0, 20)
            start = self.u32(0x272bbc), self.u32(0x272bc0)
            goal = step['x'], step['y']
            queue, visited = deque([(start, [])]), {start}
            path = None
            while queue:
                (x, y), route = queue.popleft()
                if (x, y) == goal:
                    path = route
                    break
                for dx, dy, pad in ((0, -1, 0x2000), (0, 1, 0x1000), (-1, 0, 0x800), (1, 0, 0x400)):
                    at = x + dx, y + dy
                    if at not in visited and self.call(0x121350, self.board, *at):
                        visited.add(at)
                        queue.append((at, route + [pad]))
            require(path is not None, 'No native cursor path to Korean demo placement')
            for pad in path:
                frame(pad)
                frame(0, 8)
            require((self.u32(0x272bbc), self.u32(0x272bc0)) == goal,
                    'Native demo cursor reached another cell')
            stock = struct.unpack('<H', self.read(self.board + 0x23c10, 2))[0]
            frame(0x200)
            require(struct.unpack('<H', self.read(self.board + 0x23c10, 2))[0] == stock - 1,
                    'Native demo rejected Korean placement')
            wait_ready()
        return frames

    def setup_vs(self, stage):
        self.stage, self.frame, self.steps, self.ready_at = stage, 0, [], None
        self.uc.mem_write(0x272d90, bytes(0x320))
        request = self.STACK - 0x5000
        self.uc.mem_write(request, bytes.fromhex(self.title_requests[stage]['request_hex']).ljust(0x90, b'\0'))
        self.call(0x1472c0, request, count=10_000_000)
        self.board = self.u32(0x272d94)
        base = 0x272d90
        state = self.u32(base)
        require(state == 0x272f10 and self.u32(state + 8) == self.title_requests[stage]['initial_state'],
                'Versus probe is not using the original title UI state')
        self.put(base + 8, self.call(0x122638, self.board, 0))
        self.uc.reg_write(UC_MIPS_REG_0 + 8, 28)
        self.put(base + 12, self.call(0x126098, self.board, 0, 0, 29))
        for lane in range(2):
            menu = self.call(0x126a78, self.board + 0x23c0c + lane * 0x738)
            self.put(base + 0x10 + lane * 4, menu)
            self.uc.reg_write(UC_MIPS_REG_0 + 8, 0)
            self.put(base + 0x18 + lane * 4, self.call(0x12ae38, lane + 1, 0, 14, 18))
            self.uc.reg_write(UC_MIPS_REG_0 + 8, int(self.u32(self.board + 0x232b0) == 0))
            self.call(0x1209a8, self.board, lane + 1, base + 0x3c + lane * 20, base + 0x40 + lane * 20)
        self.put(base + 0x74, self.call(0x121100, self.board))
        require(self.u32(state + 0x24) == self.title_requests[stage]['initial_turn'],
                'Native title selected another initial versus turn')
        self.put(0x273080, 1)
        self.warmup_trace = []
        for _ in range(20):
            self.tick_vs([0, 0])
            self.warmup_trace.append((self.u32(state + 8), self.u32(state + 12)))
        require(self.u32(state + 8) == 5, 'Native versus title setup did not finish during warmup')
        self.frame, self.ready_at = 0, 0

    def tick_vs(self, pads):
        self.frame += 1
        at = self.STACK - 0xa000
        self.put(at, *pads)
        self.call(0x1473e8, at)
        if self.ready_at is None and self.u32(self.u32(0x272d90) + 8) == 5:
            self.ready_at = self.frame
        controller = self.u32(0x272d9c)
        self.put(controller + 0xa98, self.u32(controller + 0xa98) + 1)
        for lane in range(2):
            self.call(0x12b328, self.u32(0x272da8 + lane * 4))
        self.call(0x122c10, self.u32(0x272d98))

    def record_vs(self, stage, rounds=2):
        from collections import deque
        from unpack_disc import require
        self.setup_vs(stage)
        frames, moves = [], []

        def frame(lane=0, pad=0, count=1):
            for _ in range(count):
                pads = [0, 0]
                pads[lane] = pad
                frames.append(pads)
                self.tick_vs(pads)

        frame(count=340)
        for turn in range(rounds * 2):
            lane = turn % 2
            cursor = self.u32(0x272da8 + lane * 4)
            coords = 0x272dcc + lane * 20
            for _ in range(1200):
                if (self.call(0x12b320, cursor) == 0 and not self.u32(0x272df8)
                        and self.u32(self.board + 0x232b4) == 1
                        and (stage != 105 or self.u32(self.u32(0x272d90) + 0x24) == lane)):
                    break
                frame()
            else:
                raise ValueError('VS demo did not return to a native ready state')
            token = self.call(0x128050, self.u32(0x272da0 + lane * 4), 0)
            start = self.u32(coords), self.u32(coords + 4)
            choices = []
            for row in self.stages.stages[stage].board.cells:
                x, y = row[1:3]
                cell = self.call(0x121380, self.board, x, y)
                if self.u32(cell + 0x14):
                    continue
                q = self.call(0x123f98, self.board, token, x, y)
                if q:
                    choices.append((abs(x - start[0]) + abs(y - start[1]), x, y))
                    self.call(0x124468, q)
            require(choices, 'VS Korean demo has no opening for its first stock letter')
            _, x, y = min(choices)
            goal = x, y
            queue, visited = deque([(start, [])]), {start}
            while queue:
                at, path = queue.popleft()
                if at == goal:
                    break
                for dx, dy, pad in ((0, -1, 0x2000), (0, 1, 0x1000), (-1, 0, 0x800), (1, 0, 0x400)):
                    to = at[0] + dx, at[1] + dy
                    if to not in visited and self.call(0x121350, self.board, *to):
                        visited.add(to)
                        queue.append((to, path + [pad]))
            else:
                raise ValueError('No native VS cursor path')
            frame(lane, 0x200)
            frame(count=20)
            for pad in path:
                frame(lane, pad)
                frame(count=8)
            require((self.u32(coords), self.u32(coords + 4)) == goal, 'VS cursor differs')
            supply = self.board + 0x23c0c + lane * 0x738
            stock = struct.unpack('<H', self.read(supply + 4, 2))[0]
            frame(lane, 0x200)
            require(struct.unpack('<H', self.read(supply + 4, 2))[0] == stock - 1,
                    'VS native demo rejected Korean placement')
            moves.append(dict(lane=lane, token=token, x=x, y=y, frame=len(frames)))
            frame(count=400)
        return frames, moves


def play(machine, raw, stage, report):
    """Decode every actual sample, then feed the native solo/VS input handlers."""
    if stage >= 100:
        machine.setup_vs(stage)
    else:
        machine.setup(stage)
    record = next(p for p in report['patch']['profiles'] if p['stage'] == stage)
    require(struct.unpack('<2I', machine.read(report['patch']['state'], 8))
            == (record['profile'], machine.board), 'Demo did not select its Korean runtime profile')
    supply = machine.board + 0x23c0c
    expected = machine.read(record['inventory'], record['inventory_count'] * 2)
    for lane in range(2 if stage >= 100 else 1):
        pointer = machine.u32(supply + lane * 0x738 + 8)
        require(machine.read(pointer, len(expected)) == expected, 'Demo loaded another supply')
    for x, y, token in record['preplaced']:
        cell = machine.call(0x121380, machine.board, x, y)
        actual = struct.unpack('<H', machine.read(machine.u32(cell + 0x14), 2))[0]
        require(token == actual, 'Demo loaded another Korean preplaced letter')
    buffer, output = machine.STACK - 0x20000, machine.STACK - 0x9000
    machine.uc.mem_write(buffer, raw)
    player = machine.call(0x136fc8, buffer, len(raw))
    require(player, 'Native demo replay object allocation failed')
    machine.call(0x137198, player)
    for _ in range(demo.ACTIVE_FRAMES):
        pads = []
        for _ in range(2 if stage >= 100 else 1):
            require(machine.call(0x1371f8, player, output) < 0x80000000,
                    'Korean replay exhausted before the native timeout')
            pads.append(machine.u32(output))
        if stage >= 100:
            machine.tick_vs(pads)
        else:
            machine.tick(pads[0])
    require(machine.call(0x1371f8, player, output) == 0xffffffff,
            'Korean replay outlasted its native timeout')
    machine.call(0x137058, player)
    words = {wid: word for word, wid in report['korean_word_ids'].items()}
    steps = [{**step, 'tile': report['alphabet'][step['token']],
              'words': [words[wid] for wid in step['word_ids']]} for step in machine.steps]
    require(steps and all(step['words'] for step in steps), 'Demo accepted no existing Korean words')
    remaining = [struct.unpack('<H', machine.read(supply + lane * 0x738 + 4, 2))[0]
                 for lane in range(2 if stage >= 100 else 1)]
    require(remaining == [record['inventory_count'] - sum(s['lane'] == lane for s in steps)
                          for lane in range(len(remaining))], 'Demo supply consumption differs')
    return dict(stage=stage, frames=demo.ACTIVE_FRAMES, samples=demo.ACTIVE_FRAMES * len(remaining),
                moves=steps, remaining_supply=remaining,
                variant=1 if stage < 100 else 0, native_ready_frame=machine.ready_at,
                native_warmup_trace=machine.warmup_trace,
                remaining_goal=machine.u32(machine.board + 0x24a84) if stage < 100 else None,
                replay_eof_at_timeout=True, korean_profile=True)


def verify_title_control(elf):
    import wwrom
    original, _ = wwrom.originals()
    from elf_probe import Elf
    source, patched = Elf(original), Elf(elf)
    regions = ((0x16b520, 0x340), (0x16b860, 0xb8), (0x16b918, 0xd8),
               (0x16b9f0, 0x3e0))
    for start, size in regions:
        require(elf[patched.offset(start):patched.offset(start) + size]
                == original[source.offset(start):source.offset(start) + size],
                'Korean demo changed native entry, timeout, interruption or exit control')
    require(struct.unpack_from('<I', elf, patched.offset(0x16bc18))[0] == 0x240206f4,
            'Native attract timeout changed')
    return dict(original_regions=[dict(address=hex(at), bytes=size) for at, size in regions],
                active_frames=1780, entry_warmup_frames=20, source_entry_exit_interrupt_preserved=True)


def verify(machine, report, dictionary=None):
    """Reuse the existing CPU/resource heap and restore it after demo-only checks."""
    from unpack_contents import wwrom_parts
    verify_title_control(machine.elf.data)
    data = json.loads(demo.RECORDINGS.read_text(encoding='utf-8'))
    replay = next(raw for row, raw in wwrom_parts(machine.elf.data, machine.rom)
                  if row['name'] == 'replay/replaypad.bin')
    probe = DemoMachine.__new__(DemoMachine)
    probe.__dict__.update(machine.__dict__)
    probe.dictionary = dictionary
    probe.allocations, probe.pending = list(machine.allocations), list(machine.pending)
    probe.install_probe_hooks()
    ram = machine.read(0x100000, 0x1f00000)
    context = machine.uc.context_save()
    results = []
    try:
        if dictionary is not None:
            probe.call(0x1147d0)
            require(probe.call(0x114828, count=5_000_000) == 0,
                    'Production Korean demo dictionary initialization failed')
            require(probe.u32(0x1c7414) == len(dictionary.words),
                    'Production Korean demo dictionary count differs')
        baseline = probe.read(0x100000, 0x1f00000)
        for row in report['patch']['korean_demos']:
            machine.uc.mem_write(0x100000, baseline)
            machine.uc.context_restore(context)
            probe.native_events = []
            stage = row['stage']
            expected = bytes.fromhex(data['stages'][str(stage)]['replay_hex'])
            require(replay[row['offset']:row['offset'] + row['bytes']] == expected,
                    'Paired build contains another Korean demo recording')
            result = play(probe, expected, stage, report)
            moves = data['stages'][str(stage)]['moves']
            require([{k: s[k] for k in ('lane', 'token', 'x', 'y', 'frame', 'word_ids')}
                     for s in result['moves']] == moves,
                    f'Korean demo {stage} native input/results differ from its recorded proof')
            if dictionary is not None:
                require(probe.native_events, 'Production Korean demo exercised no native WDT event')
                result['native_wdt_events'] = list(probe.native_events)
            results.append(result)
    finally:
        for hook in probe.hooks:
            machine.uc.hook_del(hook)
        machine.uc.mem_write(0x100000, ram)
        machine.uc.context_restore(context)
    return results


def verify_recovery(elf, rom, dictionary, stages, report):
    """Consume the unmodified production plan result; do not build a fixture."""
    import demo_audit_958
    from unpack_contents import wwrom_parts
    from mini_dictionary import Dictionary
    files = {row['name'].removeprefix('dic/'): raw for row, raw in wwrom_parts(elf, rom)
             if row['name'].startswith('dic/')}
    require(isinstance(dictionary, Dictionary) and dictionary.files == files,
            'Recovery demo dictionary is not the production WWROM dictionary')
    require(stages.data == next(raw for row, raw in wwrom_parts(elf, rom) if row['name'] == 'stage/1p.stg'),
            'Recovery demo stages are not the production WWROM stages')
    title = demo_audit_958.audit_title(elf)
    control = verify_title_control(elf)
    machine = DemoMachine(elf, rom, dictionary=dictionary)
    machine.initialize_search()
    machine.call(0x1147d0)
    require(machine.call(0x114828, count=5_000_000) == 0,
            'Production demo dictionary initialization failed')
    require(machine.u32(0x1c7414) == len(dictionary.words),
            'Production demo dictionary count differs')
    for key in ('dicdata.dix', 'dicdata.wix'):
        pointer = machine.u32(0x1c7404 if key.endswith('.dix') else 0x1c7408)
        require(machine.read(pointer, len(files[key])) == files[key],
                'Native demo loaded another production dictionary index')
    replay = next(raw for row, raw in wwrom_parts(elf, rom) if row['name'] == 'replay/replaypad.bin')
    recording = json.loads(demo.RECORDINGS.read_text(encoding='utf-8'))
    ram, context = machine.read(0x100000, 0x1f00000), machine.uc.context_save()
    results = []
    for row in report['patch']['korean_demos']:
        stage = row['stage']
        machine.uc.mem_write(0x100000, ram)
        machine.uc.context_restore(context)
        machine.native_events = []
        raw = bytes.fromhex(recording['stages'][str(stage)]['replay_hex'])
        require(replay[row['offset']:row['offset'] + row['bytes']] == raw,
                'Production pair does not contain the current Korean recording')
        print(f'[DEMO RECOVERY] production stage {stage}, actual title warmup, WDT event, 1780 frames', flush=True)
        result = play(machine, raw, stage, report)
        expected = recording['stages'][str(stage)]['moves']
        require([{key: step[key] for key in ('lane', 'token', 'x', 'y', 'frame', 'word_ids')}
                 for step in result['moves']] == expected,
                f'Production Korean demo {stage} differs from its recorded fixture')
        require(machine.native_events, 'Production Korean demo exercised no native word event')
        require({event['word_id'] for event in machine.native_events}
                <= {wid for step in result['moves'] for wid in step['word_ids']},
                'Native demo event does not belong to a recorded accepted placement')
        result['native_wdt_events'] = list(machine.native_events)
        results.append(result)
    demo_audit_958.require_title_timing(results, title)
    return dict(production_pair_sha256={'MINI.ELF': hashlib.sha256(elf).hexdigest(),
                                       'WWROM.DAT': hashlib.sha256(rom).hexdigest()},
                recordings_sha256=hashlib.sha256(demo.RECORDINGS.read_bytes()).hexdigest(),
                title=title, title_control=control, native=results,
                source='Actual build_958.plan(CAMPAIGN) result, no synthetic font or dictionary replacement.',
                scope='Actual title requests, solo/versus dispatch, 20 warmup + 1780 input frames, '
                      'native WDT event reads and text preparation, lookup, supply and board motion. '
                      'EE libc string helpers, HUD allocation/presentation, callback clocks, GPU/audio are modeled. '
                      'Original timeout/interruption/exit bytes retained, not a PCSX2 control test.')


def main():
    import argparse
    import wwrom
    from unpack_contents import wwrom_parts
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    elf, rom, report = fixture()
    title_control = verify_title_control(elf)
    machine = DemoMachine(elf, rom)
    print('Initializing one native resource heap', flush=True)
    machine.initialize_search()
    ram = machine.read(0x100000, 0x1f00000)
    source_replay = next(raw for row, raw in wwrom_parts(elf, rom) if row['name'] == 'replay/replaypad.bin')
    if args.record:
        data = dict(version=1, active_frames=demo.ACTIVE_FRAMES,
                    source_sha256=hashlib.sha256(source_replay).hexdigest(), stages={})
        for _, capacity, stage in demo.SLOTS:
            machine.uc.mem_write(0x100000, ram)
            description = next(p for p in report['profiles'] if p['index'] == stage)
            print(f'Recording bounded Korean native demo {stage}', flush=True)
            if stage < 100:
                frames = machine.record_solo(description, 8 if stage == 19 else 6)
            else:
                frames, _ = machine.record_vs(stage, 1)
            raw = demo.encode(frames, 2 if stage >= 100 else 1)
            require(len(raw) <= capacity, 'Korean recording exceeds its original replay slice')
            profile = next(p for p in report['compiled_profiles'] if p['stage'] == stage)
            data['stages'][str(stage)] = dict(profile_sha256=demo.fingerprint(profile, description),
                                            replay_hex=raw.hex(), moves=machine.steps)
        demo.RECORDINGS.write_text(json.dumps(data, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    patched, demos = demo.plan(elf, source_replay, report['compiled_profiles'], report['profiles'])
    data = json.loads(demo.RECORDINGS.read_text(encoding='utf-8'))
    results = []
    for row in demos:
        machine.uc.mem_write(0x100000, ram)
        stage = row['stage']
        print(f'Replaying native Korean demo {stage}: {demo.ACTIVE_FRAMES} frames', flush=True)
        result = play(machine, patched[row['offset']:row['offset'] + row['bytes']], stage, report)
        require([{k: s[k] for k in ('lane', 'token', 'x', 'y', 'frame', 'word_ids')}
                 for s in result['moves']] == data['stages'][str(stage)]['moves'],
                f'Native re-recording {stage} is not deterministic')
        results.append(result)
    proof = dict(native=results, replay_resource_bytes=len(patched), original_replay_bytes=len(source_replay),
                 title_control=title_control,
                 stage_count=len(machine.stages.stages), new_word_ids=0,
                 source_hashes={'MINI.ELF': hashlib.sha256(wwrom.originals()[0]).hexdigest(),
                                'WWROM.DAT': hashlib.sha256(wwrom.originals()[1]).hexdigest()},
                 scope='One focused in-memory build/resource heap. Native replay decode, pad edges, pick/place, '
                       'wide lookup, supply removal, goal checks and board motion. Synthetic glyph descriptors; '
                       'WDT event metadata/presentation, rendering, audio, gameplay clocks and HUD modeled. '
                       'Original attract timeout, interruption and exit instructions retained; no GPU or PCSX2.')
    output = WORK / 'demo-proof'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'native-proof-recovery-fixture.json').write_text(json.dumps(proof, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'stages': [r['stage'] for r in results],
                      'moves': [len(r['moves']) for r in results], 'proof': str(output / 'native-proof-recovery-fixture.json')}), flush=True)


if __name__ == '__main__':
    main()
