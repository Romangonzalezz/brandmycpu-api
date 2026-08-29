import hashlib

from django.conf import settings


def client_ip(request) -> str:
    """La dirección del visitante, mirando primero el proxy de Railway."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def hash_ip(ip: str | None) -> str:
    """Los clicks son agregados y anónimos, así que la IP cruda no se guarda.

    La sal es un secreto de entorno: sin ella, el hash de una IPv4 se revierte
    por fuerza bruta sobre el espacio de 4 mil millones de direcciones, o sea
    que no sería un hash sino una codificación.
    """
    if not ip:
        return ''
    return hashlib.sha256(f'{settings.IP_HASH_SALT}:{ip}'.encode()).hexdigest()
