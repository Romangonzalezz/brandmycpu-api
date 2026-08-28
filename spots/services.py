"""
Integración con DodoPayments replicada de la que ya está testeada y en
producción en Saas-Rank (saasrank.payments.providers.dodo).

- Checkout: POST {api_base}/checkouts con `product_cart[].product_id`
  (producto pay-what-you-want, el precio viaja en `amount` en centavos).
- Firma del webhook: Standard Webhooks (https://www.standardwebhooks.com):
  HMAC-SHA256 sobre ``{id}.{timestamp}.{body}``, base64, con la clave en
  `whsec_<base64>`. Headers `webhook-id`, `webhook-timestamp`,
  `webhook-signature: v1,<sig>`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Mapping

import requests
from django.conf import settings

logger = logging.getLogger('brandmycpu.dodo')

#: Rechaza toda entrega más vieja que esto (anti replay).
MAX_SIGNATURE_AGE_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 15

DATAFAST_PAYMENTS_URL = 'https://datafa.st/api/v1/payments'

SUCCESS_EVENTS = {'payment.succeeded'}

#: El pago nunca entró. Sólo aplica si el spot todavía no está confirmado: un
#: `failed` que llega tarde no puede tumbar un cobro que sí prosperó.
FAILURE_EVENTS = {'payment.failed', 'payment.cancelled'}

#: La plata se va después de haber entrado. Esto SÍ revierte un spot
#: confirmado: si no, un reembolso queda contando en el goal para siempre y el
#: sticker se imprime igual. Una disputa se puede ganar; mientras esté abierta
#: no la contamos, y si se resuelve a favor se vuelve a confirmar a mano.
REVERSAL_EVENTS = {'refund.succeeded', 'dispute.opened'}


class PaymentError(Exception):
    """El proveedor rechazó el checkout o no se pudo contactar."""


class VerificationError(Exception):
    """El webhook no pasó la verificación de firma."""


def _api_base() -> str:
    return (
        'https://live.dodopayments.com'
        if settings.DODO_SERVER == 'live'
        else 'https://test.dodopayments.com'
    )


# ── Checkout ────────────────────────────────────────────────────────────────
def create_checkout(
    *,
    amount_cents: int,
    return_url: str,
    reference: str,
    metadata: Mapping[str, str] | None = None,
    email: str = '',
) -> dict[str, str]:
    """Crea un checkout de Dodo y devuelve {checkout_url, session_id}."""
    api_key = settings.DODO_API_KEY
    product_id = settings.DODO_PRODUCT_ID
    if not api_key or not product_id:
        raise PaymentError(
            'Dodo no está configurado: seteá DODO_API_KEY y DODO_PRODUCT_ID.'
        )

    payload: dict[str, Any] = {
        'product_cart': [
            {
                'product_id': product_id,
                'quantity': 1,
                # Solo se respeta si el producto es pay-what-you-want.
                'amount': amount_cents,
            }
        ],
        'return_url': return_url,
        # La referencia viaja con el pago y vuelve en el webhook: así el settle
        # identifica el spot sin confiar en el navegador.
        'metadata': {'reference': reference, **(metadata or {})},
    }
    if email:
        payload['customer'] = {'email': email}

    try:
        response = requests.post(
            f'{_api_base()}/checkouts',
            json=payload,
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise PaymentError(f'No se pudo contactar Dodo: {exc}') from exc

    if response.status_code >= 400:
        raise PaymentError(
            f'Dodo rechazó el checkout ({response.status_code}): {response.text[:400]}'
        )

    data = response.json()
    url = data.get('checkout_url')
    session_id = data.get('session_id')
    if not url:
        raise PaymentError('Dodo devolvió un checkout sin checkout_url.')

    return {'checkout_url': url, 'session_id': str(session_id or '')}


# ── Webhook (Standard Webhooks) ─────────────────────────────────────────────
def _verify_standard_webhooks(
    body: bytes, headers: Mapping[str, str], secret: str
) -> None:
    """HMAC-SHA256 sobre ``{id}.{timestamp}.{body}``, base64, `whsec_`."""
    webhook_id = headers.get('webhook-id', '')
    timestamp = headers.get('webhook-timestamp', '')
    signature_header = headers.get('webhook-signature', '')

    if not (webhook_id and timestamp and signature_header):
        raise VerificationError('Faltan headers de firma del webhook.')

    try:
        age = abs(time.time() - float(timestamp))
    except ValueError as exc:
        raise VerificationError('Timestamp de webhook inválido.') from exc
    if age > MAX_SIGNATURE_AGE_SECONDS:
        raise VerificationError('Webhook fuera de la ventana anti-replay.')

    try:
        key = base64.b64decode(secret.removeprefix('whsec_'))
    except Exception as exc:  # noqa: BLE001 - fallo de decode = config errónea
        raise VerificationError('DODO_WEBHOOK_SECRET no es base64 válido.') from exc

    signed = b'.'.join([webhook_id.encode(), timestamp.encode(), body])
    expected = base64.b64encode(
        hmac.new(key, signed, hashlib.sha256).digest()
    ).decode()

    for part in signature_header.split(' '):
        _, _, candidate = part.partition(',')
        if candidate and hmac.compare_digest(candidate, expected):
            return
    raise VerificationError('La firma del webhook no coincide.')


def parse_webhook(body: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
    """Verifica la firma y devuelve el evento normalizado."""
    verify_standard = settings.DODO_WEBHOOK_SECRET
    if not verify_standard:
        raise VerificationError('DODO_WEBHOOK_SECRET no está configurado.')
    _verify_standard_webhooks(body, headers, verify_standard)

    try:
        event: dict[str, Any] = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f'Body de webhook inválido: {exc}') from exc

    event_type = str(event.get('type', ''))
    data = event.get('data') or {}
    metadata = data.get('metadata') or {}

    return {
        'event_id': str(headers.get('webhook-id') or data.get('payment_id') or ''),
        'event_type': event_type,
        'is_succeeded': event_type in SUCCESS_EVENTS,
        'is_failed': event_type in FAILURE_EVENTS,
        'is_reversed': event_type in REVERSAL_EVENTS,
        'reference': str(metadata.get('reference', '')),
        'payment_id': str(data.get('payment_id') or ''),
        'amount_cents': int(data.get('settlement_amount') or data.get('total_amount') or 0),
        # Del pago, no supuesta: reportar USD sobre un cobro en otra moneda
        # ensucia el globo de ingresos de DataFast.
        'currency': str(data.get('settlement_currency') or data.get('currency') or 'USD').upper(),
    }

# ── DataFast (Payments API) ─────────────────────────────────────────────────
def report_payment_to_datafast(
    *,
    amount_cents: int,
    transaction_id: str,
    visitor_id: str = '',
    email: str = '',
    name: str = '',
    currency: str = 'USD',
) -> bool:
    """Reporta un pago confirmado a DataFast para su globo de ingresos.

    Best-effort: DataFast es analítica, no puede tumbar la confirmación de un
    spot ya pagado. Devuelve True sólo si el POST salió bien.
    """
    api_key = settings.DATAFAST_API_KEY
    if not api_key:
        return False

    payload: dict[str, Any] = {
        'amount': round(amount_cents / 100, 2),
        'currency': currency or 'USD',
        # Mismo id de pago en cada reintento: DataFast deduplica por acá.
        'transaction_id': transaction_id,
    }
    if visitor_id:
        payload['datafast_visitor_id'] = visitor_id
    if email:
        payload['email'] = email
    if name:
        payload['customer_name'] = name

    try:
        response = requests.post(
            DATAFAST_PAYMENTS_URL,
            json=payload,
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning('DataFast no recibió el pago %s: %s', transaction_id, exc)
        return False

    if response.status_code >= 400:
        logger.warning(
            'DataFast rechazó el pago %s (%s): %s',
            transaction_id, response.status_code, response.text[:200],
        )
        return False
    return True
