"""
Probar que un post en X existe y que fue escrito para este reclamo.

Por qué existe este módulo: un lugar gratis en el vidrio vale plata, y lo único
que separa siete lugares de siete bots es que tomar uno cueste algo. Postear en
público, desde una cuenta real, es ese costo.

── Cómo verifica, y por qué no de la forma obvia ─────────────────────────

El chequeo obvio es "¿el post enlaza a brandmycpu.lol?". No se puede: X reescribe
toda URL a un shortlink t.co, así que el destino no está en la respuesta, y el
texto del enlace tampoco. Resolver el t.co sería seguir una redirección hacia un
host que eligió un desconocido, para una garantía más débil que la de abajo.

El texto del post, en cambio, vuelve tal cual. Así que la prueba son dos frases
que tienen que aparecer en él: el nombre del sitio y el asiento que se reclama.

    "just claimed free spot #3 on BrandMyCPU"
                                ~~          ~~~~~~~~~~

Hacen falta las dos. El nombre solo dejaría que cualquier post que alguna vez
mencionó el sitio pague un lugar; el número solo es una cadena que aparece en la
mitad de los posts de internet.

── Lo que esto NO prueba, y a sabiendas ──────────────────────────────────

Las frases son públicas y adivinables, así que esto no ata un post a la persona
que reclama. Dos cosas lo mantienen honesto igual: el número del asiento está en
el post, y un asiento se toma una sola vez, así que un post paga como mucho un
lugar y una vez que el #3 no está, ese post no compra nada. Queda que alguien
pegue el post de un tercero antes de que ese tercero reclame. Para siete lugares
eso es una carrera, no un agujero, y la alternativa (un código por persona que
nadie pueda reutilizar) le cuesta a la campaña justamente aquello para lo que
existe: un post que cualquiera pueda leer y repetir.

── Lo que esto no es ─────────────────────────────────────────────────────

No es identidad. Cualquiera se hace una cuenta y postea. Cuesta una declaración
pública desde un handle real, que es suficiente fricción para siete lugares y es
todo lo que dice ser.

oEmbed no está autenticado y no necesita API key ni plan pago. Contesta sólo por
posts PÚBLICOS, y eso es una ventaja: el post de una cuenta protegida no es la
declaración pública por la que se cambió el lugar, y vuelve 404 igual que uno
borrado.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import unescape

import requests

log = logging.getLogger('brandmycpu.tweets')

OEMBED_URL = 'https://publish.x.com/oembed'
TIMEOUT_SECONDS = 8
USER_AGENT = 'BrandMyCPUBot/1.0 (+https://brandmycpu.lol)'

#: Los dos hosts, con y sin www. El id es lo único que se conserva.
STATUS_URL = re.compile(
    r'^https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]{1,15}/status/(\d{1,25})',
    re.IGNORECASE,
)

#: El sitio, como sea que a alguien se le ocurra escribirlo. Se compara sobre
#: una forma sin nada que no sea letra o dígito, así "BrandMyCPU",
#: "brand my cpu" y "brand-my-cpu" son la misma palabra: nadie debería perder
#: un lugar por un guion.
SITE_WORD = 'brandmycpu'


class TweetError(ValueError):
    """El post no prueba lo que tiene que probar. El mensaje se le muestra."""


@dataclass(frozen=True)
class VerifiedTweet:
    tweet_id: str
    author_handle: str
    text: str


def phrase_for(seat: int) -> str:
    """La línea que el post tiene que llevar, tal como la lee una persona."""
    return f'free spot #{seat} on BrandMyCPU'


def _squeeze(text: str) -> str:
    """Sólo letras, dígitos y #. Guiones, espacios y mayúsculas dejan de contar."""
    return re.sub(r'[^a-z0-9#]', '', text.lower())


def verify_tweet(*, tweet_url: str, seat: int) -> VerifiedTweet:
    """Trae el post y prueba que nombra al sitio y a este asiento.

    Levanta TweetError con un mensaje pensado para que lo lea la persona que
    acaba de postear.
    """
    match = STATUS_URL.match((tweet_url or '').strip())
    if not match:
        raise TweetError('That is not a link to a post. Copy the post URL from X.')

    tweet_id = match.group(1)

    try:
        response = requests.get(
            OEMBED_URL,
            params={'url': f'https://x.com/i/status/{tweet_id}', 'omit_script': '1'},
            headers={'User-Agent': USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Nuestro, no de la persona. Nunca decirle que su post real no existe
        # porque a nosotros se nos venció una request.
        log.warning('oembed inalcanzable: %s', exc)
        raise TweetError('Could not reach X just now. Try again in a moment.') from exc

    if response.status_code == 404:
        raise TweetError(
            'X does not show that post. It may be deleted, or the account may be private.'
        )
    if response.status_code >= 400:
        log.warning('oembed devolvió %s', response.status_code)
        raise TweetError('Could not read that post from X. Try again in a moment.')

    try:
        data = response.json()
    except ValueError as exc:
        raise TweetError('Could not read that post from X. Try again in a moment.') from exc

    text = _plain_text(str(data.get('html') or ''))
    squeezed = _squeeze(text)

    if SITE_WORD not in squeezed:
        raise TweetError('That post does not mention BrandMyCPU.')
    if f'#{seat}' not in squeezed:
        raise TweetError(
            f'That post does not say #{seat}, which is the spot you are claiming.'
        )

    return VerifiedTweet(
        tweet_id=tweet_id,
        author_handle=str(data.get('author_url') or '').rstrip('/').rsplit('/', 1)[-1],
        text=text[:500],
    )


def _plain_text(html: str) -> str:
    """oEmbed devuelve un <blockquote>. Queremos lo que leería una persona."""
    return unescape(re.sub(r'<[^>]+>', ' ', html))
