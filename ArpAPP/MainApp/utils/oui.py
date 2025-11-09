
import csv
import os
import re
from functools import lru_cache


OUI_CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'mac-vendors-export.csv')

_oui_map = None

def _normalize_oui(oui_raw: str) -> str:

    if not oui_raw:
        return ''
    parts = re.split(r'[^0-9A-Fa-f]+', oui_raw)
    parts = [p.zfill(2).lower() for p in parts if p != '']
    if len(parts) == 3:
        return ':'.join(parts)

    s = re.sub(r'[^0-9A-Fa-f]', '', oui_raw)
    if len(s) >= 6:
        s = s[:6]
        return ':'.join([s[i:i+2].lower() for i in range(0, 6, 2)])
    return oui_raw.lower()

def load_oui_csv(path: str = None):

    global _oui_map
    if _oui_map is not None:
        return _oui_map
    path = path or OUI_CSV_PATH
    mapping = {}
    if not os.path.exists(path):

        alt = "/mnt/data/mac-vendors-export.csv"
        if os.path.exists(alt):
            path = alt
        else:

            _oui_map = mapping
            return mapping

    with open(path, newline='', encoding='utf-8', errors='replace') as fh:
        reader = csv.DictReader(fh)

        prefix_key = None
        vendor_key = None
        headers = [h.lower() for h in reader.fieldnames or []]
        for h in headers:
            if 'mac' in h and ('prefix' in h or 'oui' in h):
                prefix_key = h
            if 'vendor' in h or 'organization' in h or 'company' in h:
                vendor_key = h

        if not prefix_key:
            prefix_key = reader.fieldnames[0]
        if not vendor_key:
            vendor_key = reader.fieldnames[1] if len(reader.fieldnames) > 1 else reader.fieldnames[0]

        for row in reader:
            raw_prefix = row.get(prefix_key, '') if prefix_key in row else row.get(reader.fieldnames[0], '')
            vendor = (row.get(vendor_key, '') if vendor_key in row else row.get(reader.fieldnames[1], '')).strip()
            oui = _normalize_oui(raw_prefix)
            if oui:
                mapping[oui] = vendor

    _oui_map = mapping
    return mapping


_VENDOR_TO_TYPE_RULES = [

    (r'cisco', 'router/switch'),
    (r'juniper', 'router/switch'),
    (r'huawei', 'router/switch'),
    (r'hpe|hewlett-packard', 'switch/router'),
    (r'aruba', 'access-point/switch'),
    (r'ubiquiti', 'access-point/switch'),
    (r'mikrotik', 'router'),
    (r'tp-link|tplink', 'router'),
    (r'netgear', 'router'),
    (r'd-link|dlink', 'router'),
    (r'linksys', 'router'),
    (r'apple', 'client'),
    (r'samsung', 'client'),
    (r'google', 'client'),
    (r'acer|lenovo|dell|hp ', 'client'),
    (r'xero[xq]|xerox', 'printer'),
    (r'epson', 'printer'),
    (r'brother', 'printer'),
    (r'cannon|canon', 'printer'),
    (r'fortinet|fortigate', 'firewall'),
    (r'palo alto', 'firewall'),
    (r'checkpoint', 'firewall'),
    (r'sony', 'client'),
    (r'ricoh', 'printer'),
    (r'routerboard', 'router')

]

@lru_cache(maxsize=512)
def get_vendor_and_device_type(mac: str, csv_path: str = None):

    if not mac:
        return (None, 'unknown')
    mac = mac.strip().lower()

    parts = re.split(r'[^0-9a-fA-F]+', mac)
    parts = [p.zfill(2).lower() for p in parts if p != '']
    if len(parts) < 6:
        # invalid mac
        return (None, 'unknown')
    oui = ':'.join(parts[:3])
    mapping = load_oui_csv(csv_path)

    vendor = mapping.get(oui)
    guessed = 'unknown'
    if vendor:
        v = vendor.lower()
        for pattern, dtype in _VENDOR_TO_TYPE_RULES:
            if re.search(pattern, v):
                guessed = dtype
                break

        if guessed == 'unknown':
            if re.search(r'\b(inc|ltd|corp|computer|electronics|systems|technologies)\b', v):
                guessed = 'client'
    else:

        guessed = 'unknown'

    return (vendor, guessed)
