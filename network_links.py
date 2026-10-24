"""Link de rețea pentru fișierele din workdir-ul agentului (singurul folder
mapat — folderele legate de proiect, oriunde ar fi pe disc, nu sunt
atinse, ca să nu complicăm cu toate share-urile Samba). Fișierele robo-zero
sunt create întotdeauna pe SVL, al cărui IP ZeroTier e fix."""

import os

import path_access

_ZT_HOST = "10.22.22.2"  # IP-ul SVL în rețeaua ZeroTier "robo-net"
_SHARE_ROOT = "/srv"  # rădăcina share-ului Samba [Srv] din smb.conf
_SHARE_NAME = "Srv"


def to_network_link(local_path: str) -> str | None:
    """Întoarce `\\\\10.22.22.2\\Srv\\rest\\of\\path` pentru o cale absolută
    din workdir-ul agentului, sau None dacă fișierul e în afara lui."""
    resolved = os.path.realpath(local_path)
    workdir = os.path.realpath(path_access.BASE_WORKDIR)
    if resolved != workdir and not resolved.startswith(workdir + os.sep):
        return None
    if not resolved.startswith(_SHARE_ROOT + os.sep):
        return None  # workdir-ul a fost reconfigurat în afara /srv
    rel = resolved[len(_SHARE_ROOT):].lstrip(os.sep).replace("/", "\\")
    return f"\\\\{_ZT_HOST}\\{_SHARE_NAME}\\{rel}"
