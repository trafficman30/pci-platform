# UG405 REST routes — web process.

import logging
import os

from fastapi import APIRouter, HTTPException

from pci.ug405.protocol.config import load_ug405, sig_raw

log = logging.getLogger('pci.web.ug405')

router  = APIRouter()
_client = None


def set_client(c):
    global _client
    _client = c


@router.get("/ping")
async def ping():
    if _client is None:
        raise HTTPException(503, "ug405 client not initialised")
    ack = _client.send_command("PING")
    if ack is None:
        raise HTTPException(503, "ug405 not responding")
    return ack


@router.get("/mapping")
async def mapping():
    """Return SCN→signal mapping derived from ug405.cfg.  Same structure as
    CM5 _build_mapping_json(): {scns, control:{scn:{field:{bit:sig}}}, reply:...}"""
    cfg_path = os.environ.get('PCI_UG405_CFG') or None
    try:
        cfg = load_ug405(cfg_path)
    except FileNotFoundError:
        return {'scns': [], 'control': {}, 'reply': {}}
    out = {'scns': cfg['scns'], 'control': {}, 'reply': {}}
    for scn in cfg['scns']:
        out['control'][scn] = {
            field: {str(bit): sig for bit, sig in bits.items()}
            for field, bits in cfg['control'][scn].items()
        }
        out['reply'][scn] = {
            field: {str(bit): sig_raw(sig) for bit, sig in bits.items()}
            for field, bits in cfg['reply'][scn].items()
        }
    return out
