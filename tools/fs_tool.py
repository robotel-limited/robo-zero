"""Tool-uri de sistem de fișiere: citire, scriere, listare relativ la workdir.

Trei clase separate FsRead, FsWrite, FsList pentru operații de fișiere.
Căile relative pornesc din agent.workdir; ce e permis (workdir, foldere
legate de proiect ro/rw, fișiere cu secrete) decide path_access.py.
"""

import os
from typing import Any

import network_links
import path_access
import project_reports_store
import usage_log
from tools.tool_base import Tool, ToolResult

_READ_PAGE_CHARS = 12000


class FsRead(Tool):
    """Tool pentru citirea fișierelor text relativ la workdir."""

    name: str = "fs_read"
    description: str = (
        "Citește conținutul unui fișier text (UTF-8) din workdir sau dintr-un folder al proiectului "
        "(cale relativă la workdir sau absolută). "
        f"Fișierele mai lungi de {_READ_PAGE_CHARS} caractere se citesc pe pagini."
    )
    parameters: dict[str, Any] = {
        "path": {
            "type": "string",
            "description": "Calea fișierului (relativă la workdir sau absolută).",
        },
        "page": {"type": "integer", "description": f"Pagina de {_READ_PAGE_CHARS} caractere, de la 1 (implicit 1)."},
    }
    required: list[str] = ["path"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execută citirea fișierului."""
        path = kwargs.get("path", "")
        if not path:
            return ToolResult(message="Eroare: parametrul 'path' e obligatoriu.")

        try:
            # workdir-ul chat-ului sau un folder legat de proiect; fără fișiere cu secrete
            full_path = path_access.resolve(self.agent, path)
            denied = path_access.read_error(self.agent, full_path, path)
            if denied:
                return ToolResult(message=denied)

            # Citește fișierul
            if not os.path.exists(full_path):
                return ToolResult(message=f"Eroare: fișierul '{path}' nu există.")

            if not os.path.isfile(full_path):
                return ToolResult(message=f"Eroare: '{path}' nu e un fișier.")

            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            # fără plafon, un fișier mare intră întreg în istoric și e retrimis la fiecare iterație
            if len(content) <= _READ_PAGE_CHARS:
                return ToolResult(message=content)
            try:
                page = max(1, int(kwargs.get("page") or 1))
            except (TypeError, ValueError):
                page = 1
            pages = -(-len(content) // _READ_PAGE_CHARS)
            if page > pages:
                return ToolResult(message=f"Eroare: fișierul '{path}' are doar {pages} pagini.")
            body = content[(page - 1) * _READ_PAGE_CHARS : page * _READ_PAGE_CHARS]
            return ToolResult(message=f"[{path} — pagina {page}/{pages}, {len(content)} caractere în total]\n{body}")

        except Exception as e:
            return ToolResult(message=f"Eroare la citirea fișierului: {str(e)}")


class FsWrite(Tool):
    """Tool pentru scrierea/adăugarea în fișiere relativ la workdir."""

    name: str = "fs_write"
    description: str = (
        "Scrie sau adaugă conținut într-un fișier. Creează directoare părinte dacă lipsesc. "
        "Calea e relativă la workdir sau absolută."
    )
    parameters: dict[str, Any] = {
        "path": {
            "type": "string",
            "description": "Calea fișierului (relativă la workdir sau absolută).",
        },
        "content": {
            "type": "string",
            "description": "Conținutul de scris sau adăugat.",
        },
        "append": {
            "type": "boolean",
            "description": "True = adaugă, False (default) = suprascrie.",
        },
    }
    required: list[str] = ["path", "content"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execută scrierea fișierului."""
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        append = kwargs.get("append", False)

        if not path:
            return ToolResult(message="Eroare: parametrul 'path' e obligatoriu.")
        if content is None:
            return ToolResult(message="Eroare: parametrul 'content' e obligatoriu.")

        try:
            full_path = path_access.resolve(self.agent, path)
            # folder ro: refuz direct. În afara zonelor permise scrierea e gated
            # de agent.py (pre_check + confirmare în UI) ÎNAINTE de execute() —
            # dacă am ajuns aici, e în workdir, într-un folder rw sau aprobată.
            if path_access.write_policy(self.agent, full_path) == "ro":
                return ToolResult(
                    message=f"Eroare: '{path}' e într-un folder doar-citire (ro) al proiectului. Scriere refuzată."
                )

            # Creează directoare părinte
            parent_dir = os.path.dirname(full_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            # Scrie fișierul
            mode = "a" if append else "w"
            with open(full_path, mode, encoding="utf-8") as f:
                f.write(content)

            char_count = len(content)
            link = network_links.to_network_link(full_path)
            if link:
                if self.agent.project_id:
                    project_reports_store.record(
                        self.agent.project_id, usage_log.scope.get().get("chat_id"), full_path
                    )
                return ToolResult(message=f"Scris {char_count} caractere în {path}\nLink de rețea: {link}")
            return ToolResult(message=f"Scris {char_count} caractere în {path}")

        except Exception as e:
            return ToolResult(message=f"Eroare la scrierea fișierului: {str(e)}")

    def pre_check(self, args: dict[str, Any]) -> bool:
        """True dacă ținta scrierii e în afara workdir — agent.py cere
        confirmare în UI înainte de a apela execute()."""
        path = args.get("path", "")
        if not path:
            return False
        return path_access.write_policy(self.agent, path_access.resolve(self.agent, path)) == "confirm"


class FsList(Tool):
    """Tool pentru listarea conținutului unui director relativ la workdir."""

    name: str = "fs_list"
    description: str = (
        "Listează fișiere și subdirectoare dintr-un director (non-recursiv). "
        "Directoarele au suffix '/'. Calea e relativă la workdir sau absolută (folder al proiectului)."
    )
    parameters: dict[str, Any] = {
        "path": {
            "type": "string",
            "description": "Calea directorului (relativă la workdir, default '.').",
        }
    }
    required: list[str] = []

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execută listarea directorului."""
        path = kwargs.get("path", ".")

        try:
            # workdir-ul chat-ului sau un folder legat de proiect; fără fișiere cu secrete
            full_path = path_access.resolve(self.agent, path)
            denied = path_access.read_error(self.agent, full_path, path)
            if denied:
                return ToolResult(message=denied)

            # Listează directorul
            if not os.path.exists(full_path):
                return ToolResult(message=f"Eroare: directorul '{path}' nu există.")

            if not os.path.isdir(full_path):
                return ToolResult(message=f"Eroare: '{path}' nu e un director.")

            entries = os.listdir(full_path)
            result_lines = []

            for entry in sorted(entries):
                entry_path = os.path.join(full_path, entry)
                if os.path.isdir(entry_path):
                    result_lines.append(entry + "/")
                else:
                    result_lines.append(entry)

            if not result_lines:
                return ToolResult(message="(director gol)")

            return ToolResult(message="\n".join(result_lines))

        except Exception as e:
            return ToolResult(message=f"Eroare la listarea directorului: {str(e)}")
