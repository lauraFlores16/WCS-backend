"""Excepción común para errores de la API con estado HTTP explícito — el
equivalente de `const e = new Error(...); e.estadoHttp = 400; throw e;` que se
usaba por todo el backend Node."""
from __future__ import annotations

from typing import Optional


class ErrorAPI(Exception):
    def __init__(self, mensaje: str, estado_http: int = 500, servicio: Optional[str] = None,
                 reintentar_en_s: Optional[int] = None):
        super().__init__(mensaje)
        self.estado_http = estado_http
        self.servicio = servicio
        self.reintentar_en_s = reintentar_en_s
