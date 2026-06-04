# web/api/routes/config.py
# Config editor backends for all 4 services.
# Routes are mounted at /api prefix:
#   GET/POST /api/offline_plan_cfg
#   GET/POST /api/ug405_scns
#   GET/POST /api/rtig_cfg
#   POST     /api/rtig_rules
#   GET/POST /api/autodim_cfg

import asyncio
import configparser
import json
import logging
import os
import re

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger('pci.web.config')

router = APIRouter()

_rtig_client    = None
_autodim_client = None


def set_rtig_client(c):
    global _rtig_client
    _rtig_client = c


def set_autodim_client(c):
    global _autodim_client
    _autodim_client = c


# ── Path helpers ───────────────────────────────────────────────────────────────

_CFG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config')
)


def _cfg(name):
    return os.path.join(_CFG_DIR, name)


def _offline_plan_path():
    return _cfg('offline_plan.json')


def _ug405_cfg_path():
    return os.environ.get('PCI_UG405_CFG', _cfg('ug405.cfg'))


def _rtig_cfg_path():
    return os.environ.get('PCI_RTIG_CFG', _cfg('rtig.cfg'))


def _rtig_rules_path():
    # rules_file in rtig.cfg is relative to the config dir
    return _cfg('rtig_rules.json')


def _autodim_cfg_path():
    return os.environ.get('PCI_AUTODIM_CFG', _cfg('autodim.cfg'))


# ── Field lists (ported from CM5) ─────────────────────────────────────────────

_CTRL_BITMASK = {'Fn', 'Dn', 'SFn'}
_REPL_BITMASK = {'Gn', 'SDn', 'LFn', 'CSn', 'VSn', 'BDn', 'TPn', 'GPn', 'SCn'}
_CTRL_SINGLE  = ['DX', 'GO', 'FM', 'TS', 'SO', 'MO', 'PV', 'PX',
                 'SG', 'LO', 'LL', 'TO', 'HI', 'CP', 'EP', 'FF']
_REPL_SINGLE  = ['MC', 'CF', 'DF', 'GX', 'FC', 'HC', 'WI', 'CS', 'RR', 'FR',
                 'SB', 'LC', 'PC', 'PR', 'CG', 'GR1', 'LE', 'RF1', 'RF2',
                 'EV', 'VC', 'VQ', 'CA', 'CR', 'CL', 'TF', 'VO', 'CO', 'EC',
                 'MR', 'MF', 'ML']


# ── Editor 1 — Offline plan ───────────────────────────────────────────────────

def _parse_offline_plan_cfg():
    with open(_offline_plan_path()) as f:
        raw = json.load(f)

    settings = raw.get('settings', {})
    raw_scns = raw.get('scns', {})

    try:
        from pci.ug405.protocol.config import load_ug405
        ug405_cfg = load_ug405(_ug405_cfg_path())
    except Exception:
        ug405_cfg = None

    scns_out = {}
    for scn_name, scn_data in raw_scns.items():
        plan_cols = []
        if ug405_cfg and scn_name in ug405_cfg.get('control', {}):
            ctrl = ug405_cfg['control'][scn_name]
            for field in list(_CTRL_BITMASK) + _CTRL_SINGLE:
                if field in ctrl and ctrl[field]:
                    bits = sorted(ctrl[field].keys()) if field in _CTRL_BITMASK else None
                    plan_cols.append({'field': field, 'bits': bits})

        plans_out = {}
        for plan_name, plan in scn_data.get('plans', {}).items():
            offsets = []
            for off in plan.get('offsets', []):
                entry = {'at': off['at']}
                for k in ('fn', 'dn', 'sfn'):
                    if k in off:
                        v = off[k]
                        entry[k] = int(v, 0) if isinstance(v, str) else int(v)
                for k in ('go', 'mo', 'pv', 'px', 'ts', 'so', 'dx', 'fm'):
                    if k in off:
                        entry[k] = int(off[k])
                offsets.append(entry)
            plans_out[plan_name] = {'cycle_secs': plan['cycle_secs'], 'offsets': offsets}

        scns_out[scn_name] = {'plan_cols': plan_cols, 'plans': plans_out}

    return {'settings': settings, 'timetable': raw.get('timetable', []), 'scns': scns_out}


def _write_offline_plan_cfg(data):
    settings = data.get('settings', {})
    bt = settings.get('base_time', '')
    m = re.match(r'(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2})', bt)
    if m:
        settings = dict(settings)
        settings['base_time'] = (
            f'{m.group(1)}{m.group(2)}{m.group(3)} {m.group(4)}{m.group(5)}'
        )

    timetable = []
    for entry in data.get('timetable', []):
        timetable.append({
            'days':  entry.get('days', []),
            'start': entry.get('start', '0000').replace(':', ''),
            'plan':  entry.get('plan') or None,
        })

    plan_json = {'settings': settings, 'timetable': timetable, 'scns': {}}
    for scn_name, scn_data in data.get('scns', {}).items():
        plans = {}
        for plan_name, plan in scn_data.get('plans', {}).items():
            offsets = []
            for off in plan.get('offsets', []):
                entry = {'at': int(off['at'])}
                for k in ('fn', 'dn', 'sfn'):
                    if k in off:
                        v = off[k]
                        v = int(v, 0) if isinstance(v, str) else int(v)
                        entry[k] = hex(v)
                for k in ('go', 'mo', 'pv', 'px', 'ts', 'so', 'dx', 'fm'):
                    if k in off:
                        v = off[k]
                        if int(v, 0) if isinstance(v, str) else int(v):
                            entry[k] = 1
                offsets.append(entry)
            if plan_name.strip():
                plans[plan_name.strip()] = {
                    'cycle_secs': int(plan['cycle_secs']),
                    'offsets':    offsets,
                }
        plan_json['scns'][scn_name] = {'plans': plans}

    with open(_offline_plan_path(), 'w') as f:
        json.dump(plan_json, f, indent=2)


@router.get("/offline_plan_cfg")
async def get_offline_plan_cfg():
    try:
        return _parse_offline_plan_cfg()
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/offline_plan_cfg")
async def post_offline_plan_cfg(request: Request):
    try:
        data = await request.json()
        _write_offline_plan_cfg(data)
        log.info("offline_plan.json updated via web UI")
        return {'ok': True}
    except Exception as e:
        raise HTTPException(400, str(e))


# ── Editor 2 — SCN signal mappings ────────────────────────────────────────────

def _parse_ug405_scns():
    scns = []
    cur  = None
    with open(_ug405_cfg_path()) as f:
        for raw_line in f:
            line = raw_line.split('#')[0].strip()
            if not line:
                continue
            if line.upper() == '[SCN]':
                cur = {'name': '', 'control': [], 'reply': []}
                scns.append(cur)
                continue
            if line.startswith('['):
                cur = None
                continue
            if cur is None or '=' not in line:
                continue
            k, v = [x.strip() for x in line.split('=', 1)]
            inverted = k.endswith('!')
            if inverted:
                k = k[:-1].strip()
            v = v.strip()
            if k.lower() == 'name':
                cur['name'] = v
                continue
            m = re.match(r'(utcControl|utcReply)([A-Za-z0-9]+?)(?:\[(\d+)\])?$', k)
            if not m:
                continue
            direction, field, bit = m.group(1), m.group(2), m.group(3)
            bit = int(bit) if bit else None
            if direction == 'utcControl':
                cur['control'].append({'field': field, 'bit': bit, 'signal': v})
            else:
                cur['reply'].append(
                    {'field': field, 'bit': bit, 'signal': v, 'inverted': inverted}
                )

    return {
        'scns':         scns,
        'ctrl_bitmask': sorted(_CTRL_BITMASK),
        'ctrl_single':  sorted(_CTRL_SINGLE),
        'repl_bitmask': sorted(_REPL_BITMASK),
        'repl_single':  sorted(_REPL_SINGLE),
    }


def _write_ug405_scns(scns):
    with open(_ug405_cfg_path()) as f:
        lines = f.readlines()

    header, live = [], []
    zone = 'header'
    for line in lines:
        tag = line.split('#')[0].strip().upper()
        if tag == '[SCN]':
            zone = 'scn'
        elif tag == '[LIVE]':
            zone = 'live'
        elif tag.startswith('[') and zone == 'header':
            pass  # other sections before first [SCN] stay in header

        if zone == 'header':
            header.append(line)
        elif zone == 'live':
            live.append(line)

    out = header[:]
    for scn in scns:
        out += ['\n', '[SCN]\n', f'name = {scn["name"]}\n']
        for r in scn.get('reply', []):
            op  = '!=' if r.get('inverted') else ' ='
            bit = f'[{r["bit"]}]' if r.get('bit') is not None else ''
            out.append(f'utcReply{r["field"]}{bit:<6}{op} {r["signal"]}\n')
        if scn.get('control'):
            out.append('\n')
        for r in scn.get('control', []):
            bit = f'[{r["bit"]}]' if r.get('bit') is not None else ''
            out.append(f'utcControl{r["field"]}{bit:<4}= {r["signal"]}\n')
    out.append('\n\n')
    out.extend(live)

    with open(_ug405_cfg_path(), 'w') as f:
        f.writelines(out)


@router.get("/ug405_scns")
async def get_ug405_scns():
    try:
        return _parse_ug405_scns()
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/ug405_scns")
async def post_ug405_scns(request: Request):
    try:
        scns = await request.json()
        if not isinstance(scns, list):
            raise HTTPException(400, "Expected list of SCNs")
        for s in scns:
            if not s.get('name', '').strip():
                raise HTTPException(400, "Each SCN must have a name")
        # Conflict check: control signals must be unique across all SCNs
        seen = {}
        for s in scns:
            for r in s.get('control', []):
                sig = r.get('signal', '').strip()
                if not sig:
                    continue
                key = (f'{r["field"]}[{r.get("bit")}]'
                       if r.get('bit') is not None else r['field'])
                if sig in seen:
                    raise HTTPException(
                        400,
                        f'Signal conflict: {sig} used by both '
                        f'{seen[sig]} and {s["name"]}.utcControl{key}'
                    )
                seen[sig] = f'{s["name"]}.utcControl{key}'
        _write_ug405_scns(scns)
        log.info("ug405.cfg SCN mappings updated via web UI (%d SCNs)", len(scns))
        return {'ok': True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Editor 3 — RTIG config + rules ────────────────────────────────────────────

def _parse_rtig_cfg():
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    cfg.optionxform = str  # preserve key case (RTIG1 not rtig1)
    cfg.read(_rtig_cfg_path())
    settings = {
        'pulse_seconds': cfg.getfloat('rtig', 'pulse_seconds', fallback=2.0),
    }
    signal_map = dict(cfg.items('signal_map')) if cfg.has_section('signal_map') else {}
    with open(_rtig_rules_path()) as f:
        rules = json.load(f)
    return {'settings': settings, 'signal_map': signal_map, 'rules': rules}


def _write_rtig_cfg(signal_map, settings):
    pulse = float(settings.get('pulse_seconds', 2.0))
    lines = [
        '# rtig.cfg — pci.rtig configuration\n\n',
        '[rtig]\n',
        f'pulse_seconds = {pulse}\n',
        'rules_file    = rtig_rules.json\n\n',
        '[signal_map]\n',
        '# Logical names used in rtig_rules.json → actual IOBus signal names\n',
    ]
    for name, sig in signal_map.items():
        name, sig = name.strip(), sig.strip()
        if name and sig:
            lines.append(f'{name} = {sig}\n')
    with open(_rtig_cfg_path(), 'w') as f:
        f.writelines(lines)


@router.get("/rtig_cfg")
async def get_rtig_cfg():
    try:
        return _parse_rtig_cfg()
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/rtig_cfg")
async def post_rtig_cfg(request: Request):
    try:
        data = await request.json()
        _write_rtig_cfg(data.get('signal_map', {}), data.get('settings', {}))
        log.info("rtig.cfg updated via web UI")
        return {'ok': True}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/rtig_rules")
async def post_rtig_rules(request: Request):
    try:
        new_rules = await request.json()
        if not isinstance(new_rules, list):
            raise HTTPException(400, "Expected JSON array")
        valid = [r for r in new_rules if r.get('signal')]
        with open(_rtig_rules_path(), 'w') as f:
            json.dump(valid, f, indent=2)
        if _rtig_client is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _rtig_client.send_command, "RELOAD_RULES")
        log.info("RTIG rules updated via web UI (%d rules)", len(valid))
        return {'ok': True, 'rules': len(valid)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))


# ── Editor 4 — Autodim config ─────────────────────────────────────────────────

def _read_autodim_cfg():
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    cfg.read(_autodim_cfg_path())
    return {
        'enabled':               cfg.getboolean('autodim', 'enabled',               fallback=True),
        'lat':                   cfg.getfloat  ('autodim', 'lat',                   fallback=51.5074),
        'lon':                   cfg.getfloat  ('autodim', 'lon',                   fallback=-0.1278),
        'dim_offset_minutes':    cfg.getint    ('autodim', 'dim_offset_minutes',    fallback=0),
        'bright_offset_minutes': cfg.getint    ('autodim', 'bright_offset_minutes', fallback=0),
        'signal':                cfg.get       ('autodim', 'signal',               fallback='virt.dim'),
    }


def _write_autodim_cfg(data):
    lines = [
        '# autodim.cfg — pci.autodim configuration\n\n',
        '[autodim]\n',
        f'enabled               = {"true" if data["enabled"] else "false"}\n',
        f'lat                   = {data["lat"]}\n',
        f'lon                   = {data["lon"]}\n',
        f'dim_offset_minutes    = {data["dim_offset_minutes"]}\n',
        f'bright_offset_minutes = {data["bright_offset_minutes"]}\n',
        f'signal                = {data["signal"]}\n',
    ]
    with open(_autodim_cfg_path(), 'w') as f:
        f.writelines(lines)


@router.get("/autodim_cfg")
async def get_autodim_cfg():
    try:
        return _read_autodim_cfg()
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/autodim_cfg")
async def post_autodim_cfg(request: Request):
    try:
        data = await request.json()
        lat           = float(data.get('lat', 0))
        lon           = float(data.get('lon', 0))
        dim_offset    = int(data.get('dim_offset_minutes', 0))
        bright_offset = int(data.get('bright_offset_minutes', 0))
        signal        = str(data.get('signal', 'virt.dim')).strip()
        enabled       = bool(data.get('enabled', True))

        if not (-90 <= lat <= 90):
            raise HTTPException(400, "lat out of range (-90 to +90)")
        if not (-180 <= lon <= 180):
            raise HTTPException(400, "lon out of range (-180 to +180)")
        if not (-240 <= dim_offset <= 240):
            raise HTTPException(400, "dim_offset_minutes out of range (±240)")
        if not (-240 <= bright_offset <= 240):
            raise HTTPException(400, "bright_offset_minutes out of range (±240)")

        try:
            current      = _read_autodim_cfg()
            enabled_changed = current['enabled'] != enabled
        except Exception:
            enabled_changed = True

        _write_autodim_cfg({
            'enabled':               enabled,
            'lat':                   lat,
            'lon':                   lon,
            'dim_offset_minutes':    dim_offset,
            'bright_offset_minutes': bright_offset,
            'signal':                signal,
        })

        # Hot-reload all settings (lat/lon/offsets/enabled) via RELOAD_CONFIG.
        # No service restart needed for any autodim config change.
        if _autodim_client is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _autodim_client.send_command, "RELOAD_CONFIG")

        log.info("autodim.cfg updated via web UI — lat=%.4f lon=%.4f dim%+d bright%+d",
                 lat, lon, dim_offset, bright_offset)
        return {'ok': True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
