"""
============================================================================
HASHEO DE CONTRASEÑAS — puerto de backend/almacen/hash.js
============================================================================
scrypt (N=16384, r=8, p=1, 32 bytes), igual que `crypto.scryptSync` de Node.
Python usa `hashlib.scrypt`, que también se apoya en OpenSSL: mismos
parámetros → mismo resultado, así que un hash generado por el backend Node
sigue siendo válido aquí (y viceversa) si migras los JSON de `almacen/datos`.

Formato del hash:  scrypt$<sal_hex>$<hash_hex>
============================================================================
"""
from __future__ import annotations

import hashlib
import hmac
import os

_N, _R, _P, _LARGO = 16384, 8, 1, 32
# scrypt necesita bastante memoria (~128*N*r bytes); con N=16384,r=8 son unos
# 16 MiB. hashlib pide el límite en KiB.
_MAXMEM = 32 * 1024 * 1024


def hashear(password: str) -> str:
    sal = os.urandom(16)
    derivada = hashlib.scrypt(str(password).encode("utf-8"), salt=sal, n=_N, r=_R, p=_P,
                              dklen=_LARGO, maxmem=_MAXMEM)
    return f"scrypt${sal.hex()}${derivada.hex()}"


def es_hash(valor) -> bool:
    return isinstance(valor, str) and valor.startswith("scrypt$")


def verificar(password: str, guardado) -> bool:
    if not es_hash(guardado):
        return str(password) == str(guardado)

    _, sal_hex, hash_hex = guardado.split("$")
    sal = bytes.fromhex(sal_hex)
    esperado = bytes.fromhex(hash_hex)
    derivada = hashlib.scrypt(str(password).encode("utf-8"), salt=sal, n=_N, r=_R, p=_P,
                              dklen=len(esperado), maxmem=_MAXMEM)
    return hmac.compare_digest(derivada, esperado)
