# ug405/protocol/config.py
# UG405 configuration parser — adapted from /opt/CM5/core/config.py load_ug405()
#
# Loads config/ug405.cfg — SCN→signal mapping, live config, service settings.
# Control signal owner: 'pci.ug405' (written by this service via IOBus W)
# Reply signal owner:   'pci.iobus' (written by hardware drivers, we only read)

import datetime
import os
import re
from collections import defaultdict

# ── MIB column IDs ───────────────────────────────────────────────────────────

REPLY_COLS = {
    'Gn':3,  'GX':4,  'DF':5,  'FC':6,  'SCn':7, 'HC':8,  'WI':9,
    'PC':10, 'PR':11, 'CG':12, 'GR1':13,'SDn':14,'MC':15,
    'CF':16, 'LE':17, 'RR':18, 'LFn':19,'RF1':20,'RF2':21,
    'EV':22, 'VC':23, 'VQ':24, 'CA':25, 'CR':26, 'CL':27,
    'CSn':28,'TF':29, 'VSn':30,'VO':31, 'CO':32, 'EC':33,
    'CS':34, 'FR':35, 'BDn':36,'TPn':37,'SB':38, 'LC':39,
    'MR':40, 'MF':41, 'ML':42, 'GPn':25,
}

CONTROL_COLS = {
    'DX':3,  'Dn':4,  'Fn':5,  'SFn':6, 'PV':7,  'PX':8,
    'SO':9,  'SG':10, 'LO':11, 'LL':12, 'TS':13, 'FM':14,
    'TO':15, 'HI':16, 'CP':17, 'EP':18, 'GO':19, 'FF':20, 'MO':21,
}

BITMASK_REPLY   = {'Gn', 'SDn', 'LFn', 'CSn', 'VSn', 'BDn', 'TPn', 'GPn', 'SCn'}
BITMASK_CONTROL = {'Fn', 'Dn', 'SFn'}

# ── Signal name helpers ───────────────────────────────────────────────────────

def sig_raw(sig):
    """Return bare bus signal name, stripping leading '!' inversion marker."""
    return sig[1:] if sig.startswith('!') else sig

def sig_read(io, sig):
    """Read signal via io.read(), which handles '!' inversion internally."""
    return io.read(sig)

def scn_to_oid_suffix(scn):
    return f"{len(scn)}." + ".".join(str(ord(c)) for c in scn)

def sig_to_id(side, sig):
    """Convert signal name to web element ID (strips inversion marker)."""
    return f"{side}-{sig_raw(sig).replace('.', '_')}"

# ── Config parser ─────────────────────────────────────────────────────────────

_CFG_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'config')

def _default_path():
    return os.path.abspath(os.path.join(_CFG_DIR, 'ug405.cfg'))


def load_ug405(path=None):
    """
    Load config/ug405.cfg — SCN signal mappings and live config.

    Returns dict with keys:
      scns     : list of SCN name strings (in config order)
      control  : {scn: defaultdict(dict)}  field → {bit: signal}
      reply    : {scn: defaultdict(dict)}  field → {bit: signal or !signal}
      signals  : {signal_name: 'pci.ug405'} control signals owned by this service
      live     : live config dict (persisted across restarts)
      services : service behaviour settings
    """
    if path is None:
        path = _default_path()

    services = {
        'scoot_inform_on_change_only': 'true',
        'clock_jitter_check':          'disabled',
        'clock_jitter_grace':          '60',
        'io_fault_timeout':            '30',
    }

    live = {
        'ConfigLastChanged':             None,
        'InstationAddress':              '0.0.0.0',
        'InstationPort':                 1162,
        'OperationModeTimeout':          60,
        'ScootSampleReportInterval':     4,
        'ReplyByException':              0,
        'ReplyByExceptionRetryDelay':    200,
        'ReplyByExceptionRetryCount':    4,
        'ReplyByExceptionKeepAlive':     0,
        'ReplyByExceptionResendHoldoff': 1,
    }

    scns    = []
    control = {}
    reply   = {}
    signals = {}     # control signals → 'pci.ug405'

    section     = None
    current_scn = None

    with open(path) as f:
        for raw in f:
            line = raw.split('#')[0].strip()
            if not line:
                continue

            if line.startswith('['):
                section = line.strip('[]').upper()
                if section == 'SCN':
                    current_scn = None
                continue

            if '=' not in line:
                continue

            k, v = [x.strip() for x in line.split('=', 1)]

            if section == 'SERVICES':
                services[k.lower()] = v.strip().lower()
                continue

            if section == 'LIVE':
                if k == 'ConfigLastChanged':
                    live['ConfigLastChanged'] = v.strip()
                elif k in live:
                    try:
                        live[k] = int(v) if k != 'InstationAddress' else v.strip()
                    except ValueError:
                        live[k] = v.strip()
                continue

            if section == 'SCN':
                if k.lower() == 'name':
                    current_scn = v.strip()
                    if current_scn not in control:
                        scns.append(current_scn)
                        control[current_scn] = defaultdict(dict)
                        reply[current_scn]   = defaultdict(dict)
                    continue

                if current_scn is None:
                    continue

                inverted = k.endswith('!')
                if inverted:
                    k = k[:-1].strip()

                sig = v.strip()
                m = re.match(r'(utcControl|utcReply)([A-Za-z0-9]+?)(?:\[(\d+)\])?$', k)
                if not m:
                    continue

                direction = m.group(1)
                field     = m.group(2)
                bit       = int(m.group(3)) if m.group(3) else 0

                if direction == 'utcReply':
                    if field not in REPLY_COLS:
                        continue
                    reply[current_scn][field][bit] = ('!' if inverted else '') + sig
                else:
                    if field not in CONTROL_COLS:
                        continue
                    control[current_scn][field][bit] = sig
                    signals[sig] = 'pci.ug405'

    if live['ConfigLastChanged'] is None:
        live['ConfigLastChanged'] = (
            datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S') + 'Z')

    return {
        'services': services,
        'scns':     scns,
        'control':  control,
        'reply':    reply,
        'signals':  signals,
        'live':     live,
    }


def persist_live(live, path):
    """Write [LIVE] section back to ug405.cfg, replacing any existing [LIVE] block."""
    with open(path) as f:
        lines = f.readlines()

    new_lines = []
    in_live   = False
    for line in lines:
        if line.strip().upper() == '[LIVE]':
            in_live = True
            continue
        if in_live and line.strip().startswith('['):
            in_live = False
        if not in_live:
            new_lines.append(line)

    if new_lines and not new_lines[-1].endswith('\n'):
        new_lines[-1] += '\n'

    ts = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S') + 'Z'
    new_lines.append('\n[LIVE]\n')
    new_lines.append(f'ConfigLastChanged             = {ts}\n')
    new_lines.append(f'InstationAddress              = {live["InstationAddress"]}\n')
    new_lines.append(f'InstationPort                 = {live["InstationPort"]}\n')
    new_lines.append(f'OperationModeTimeout          = {live["OperationModeTimeout"]}\n')
    new_lines.append(f'ScootSampleReportInterval     = {live["ScootSampleReportInterval"]}\n')
    new_lines.append(f'ReplyByException              = {live["ReplyByException"]}\n')
    new_lines.append(f'ReplyByExceptionRetryDelay    = {live["ReplyByExceptionRetryDelay"]}\n')
    new_lines.append(f'ReplyByExceptionRetryCount    = {live["ReplyByExceptionRetryCount"]}\n')
    new_lines.append(f'ReplyByExceptionKeepAlive     = {live["ReplyByExceptionKeepAlive"]}\n')
    new_lines.append(f'ReplyByExceptionResendHoldoff = {live["ReplyByExceptionResendHoldoff"]}\n')

    with open(path, 'w') as f:
        f.writelines(new_lines)
