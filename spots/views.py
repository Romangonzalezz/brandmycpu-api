import logging
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import services
from .models import Spot, Visitor
from .serializers import (
    ActivitySerializer,
    GoalSerializer,
    SpotSerializer,
    _overlaps,
)

logger = logging.getLogger('brandmycpu.api')

# Ventana de "visitante live": últimos 30 minutos
LIVE_WINDOW_MINUTES = 30

#: Clave del advisory lock que serializa las compras.
#:
#: La validación de solapamiento lee los spots y después inserta. Dos personas
#: eligiendo el mismo hueco en el mismo instante pasan las dos la lectura y
#: pagan las dos por el mismo centímetro de vidrio. `select_for_update` no
#: alcanza: no hay fila que trabar cuando el lugar está libre.
#:
#: ponytail: lock global sobre todo el vidrio. Las compras son raras y el
#: recurso es uno solo, así que no hace falta granularidad; si algún día hay
#: cola, la clave puede pasar a ser el hueco.
GLASS_LOCK_KEY = 815234


def _confirm_and_displace(spot: Spot) -> list[int]:
    """Confirma un spot pagado y saca del vidrio a los stickers que tapa.

    El outbid desplaza y NO reembolsa: el que pierde el lugar se queda con el
    cobro hecho. Por eso su fila pasa a `outbid` en vez de borrarse, y por eso
    `goal()` la sigue sumando: esa plata entró y nunca se devolvió, así que la
    barra no puede bajar sola cuando alguien es superado.

    Se recalcula el solapamiento acá y no en el checkout porque el estado del
    vidrio pudo cambiar entre el pago y el webhook. No puede haber aparecido un
    competidor por el mismo hueco: el serializer rechaza superar un `pending`
    vivo, así que mientras este checkout estuvo abierto el lugar era suyo.

    Devuelve los ids desplazados. Llamar dentro de una transacción.
    """
    spot.status = 'confirmed'
    spot.save(update_fields=['status', 'payment_id'])

    losers = [
        o.pk
        for o in Spot.objects.filter(status__in=['confirmed', 'placed']).exclude(pk=spot.pk)
        if _overlaps(
            spot.position_x, spot.position_y, spot.width_cm, spot.height_cm,
            o.position_x, o.position_y, o.width_cm, o.height_cm,
        )
    ]
    if losers:
        Spot.objects.filter(pk__in=losers).update(status='outbid')
        logger.warning(
            'Spot %s (%s centavos) desplazó por outbid a %s. Sin reembolso.',
            spot.id, spot.price_paid, losers,
        )
    return losers


# ── Visitors ────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([AllowAny])
def visitor_heartbeat(request):
    """Recibe el heartbeat del frontend (session_id en localStorage)."""
    session_id = (request.data.get('session_id') or '').strip()
    if not session_id:
        return Response(
            {'error': 'session_id es requerido'}, status=status.HTTP_400_BAD_REQUEST
        )
    if len(session_id) > 100:
        session_id = session_id[:100]
    Visitor.objects.update_or_create(
        session_id=session_id, defaults={'last_seen': timezone.now()}
    )
    return Response({'ok': True})


@api_view(['GET'])
@permission_classes([AllowAny])
def visitor_count(request):
    """Live = visitantes con heartbeat en los últimos 30 min. Total acumulado."""
    cutoff = timezone.now() - timedelta(minutes=LIVE_WINDOW_MINUTES)
    live = Visitor.objects.filter(last_seen__gte=cutoff).count()
    total = Visitor.objects.count()
    return Response({'live': live, 'total': total})


# ── Spots ───────────────────────────────────────────────────────────────────
@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def spots_endpoint(request):
    if request.method == 'GET':
        queryset = Spot.objects.all()
        state = request.query_params.get('status')
        if state in dict(Spot.STATUS_CHOICES):
            queryset = queryset.filter(status=state)
        # El context lleva el request: sin él logo_url sale relativa y el
        # frontend, que vive en otro dominio, la resuelve contra sí mismo.
        return Response(
            SpotSerializer(queryset, many=True, context={'request': request}).data
        )

    # POST — crea el spot pending y un checkout de DodoPayments
    serializer = SpotSerializer(data=request.data)
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [GLASS_LOCK_KEY])
        # Validar y guardar bajo el mismo lock: entre el chequeo de solapamiento
        # y el insert no puede colarse otra compra.
        if not serializer.is_valid():
            # Sin esto el log sólo dice "Bad Request: /api/spots/": un intento
            # de compra que falla y no deja rastro de por qué.
            logger.warning('POST /api/spots/ rechazado: %s', serializer.errors)
            raise serializers.ValidationError(serializer.errors)
        spot = serializer.save()

    # El checkout queda FUERA de la transacción: es una llamada de red y no se
    # tiene una transacción abierta esperando a un tercero.

    if settings.FAKE_PAYMENTS:
        # Mismo recorrido que un pago real: el frontend redirige a
        # return_url?paid=<id>, ve el spot confirmado y abre el diálogo de X.
        with transaction.atomic():
            _confirm_and_displace(spot)
        logger.warning('FAKE_PAYMENTS: spot %s confirmado sin cobrar.', spot.id)
        return Response(
            {
                'id': spot.id,
                'payment_url': f'{settings.DODO_RETURN_URL}?paid={spot.id}',
                'session_id': 'fake',
                'status': spot.status,
            },
            status=status.HTTP_201_CREATED,
        )

    try:
        checkout = services.create_checkout(
            amount_cents=spot.price_paid,
            # El marcador es nuestro, no de Dodo: al volver, el frontend
            # sabe qué spot se pagó y ofrece postearlo en X.
            return_url=f'{settings.DODO_RETURN_URL}?paid={spot.id}',
            reference=str(spot.id),
            email=(request.data.get('email') or '').strip(),
        )
    except services.PaymentError as exc:
        logger.error('Dodo no inició el checkout del spot %s: %s', spot.id, exc)
        return Response(
            {'error': 'No se pudo iniciar el pago con DodoPayments.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(
        {
            'id': spot.id,
            'payment_url': checkout['checkout_url'],
            'session_id': checkout.get('session_id', ''),
            'status': spot.status,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET'])
@permission_classes([AllowAny])
def spot_activity(request):
    """Últimos spots confirmados (feed). Paginado simple por `page`."""
    queryset = Spot.objects.filter(status='confirmed')
    try:
        page = max(1, int(request.query_params.get('page', 1)))
        page_size = min(50, int(request.query_params.get('page_size', 10)))
    except (TypeError, ValueError):
        page, page_size = 1, 10

    start = (page - 1) * page_size
    end = start + page_size
    items = queryset[start:end]
    total = queryset.count()

    return Response({
        'count': total,
        'page': page,
        'results': ActivitySerializer(items, many=True).data,
    })


def _meta_headers(request) -> dict:
    """Convierte request.META (HTTP_*) a headers con nombres en minúscula."""
    headers = {}
    for key, value in request.META.items():
        if key.startswith('HTTP_'):
            headers[key[5:].replace('_', '-').lower()] = value
    return headers


def _resolve_spot(reference: str, payment_id: str) -> Spot | None:
    """Busca el spot por nuestra referencia (id) o, en su defecto, por payment_id."""
    if reference and reference.isdigit():
        try:
            return Spot.objects.get(pk=int(reference))
        except Spot.DoesNotExist:
            pass
    if payment_id:
        return (
            Spot.objects.filter(payment_id=payment_id).order_by('-created_at').first()
        )
    return None


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def spot_webhook(request):
    """Webhook de DodoPayments (Standard Webhooks) → confirma el spot."""
    try:
        event = services.parse_webhook(request.body, _meta_headers(request))
    except services.VerificationError as exc:
        logger.warning('Webhook rechazado: %s', exc)
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    spot = _resolve_spot(event['reference'], event['payment_id'])
    updated = False

    if spot and event['is_succeeded']:
        if event['payment_id']:
            spot.payment_id = event['payment_id']
        if spot.status != 'confirmed':
            updated = True
            # El desplazamiento va en la misma transacción que la confirmación:
            # nunca puede quedar el que perdió fuera del vidrio sin que el que
            # pagó haya entrado, ni al revés.
            with transaction.atomic():
                _confirm_and_displace(spot)
        else:
            spot.save(update_fields=['status', 'payment_id'])

        # Sólo en la transición. Dodo reintenta la entrega, y aunque DataFast
        # deduplica por transaction_id, cada reintento nos costaba un POST de
        # ida y vuelta antes de poder responderle.
        # Analítica, no parte del settle: si DataFast falla, el spot ya quedó
        # confirmado igual.
        if updated:
            services.report_payment_to_datafast(
                amount_cents=event['amount_cents'] or spot.price_paid,
                transaction_id=event['payment_id'] or f'spot-{spot.id}',
                visitor_id=spot.datafast_visitor_id,
                name=spot.brand_name,
                currency=event['currency'],
            )

    elif spot and event['is_reversed']:
        # Un reembolso o una disputa sí bajan un spot confirmado: la plata se
        # fue, no puede seguir sumando al goal.
        if spot.status != 'pending':
            spot.status = 'pending'
            spot.save(update_fields=['status'])
            updated = True

    elif spot and event['is_failed']:
        if spot.status != 'confirmed':
            spot.status = 'pending'
            spot.save(update_fields=['status'])
            updated = True

    return Response({'ok': True, 'updated': updated})


# ── Goal / progreso ────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([AllowAny])
def goal(request):
    """Objetivo de recaudación y progreso (price_paid en centavos)."""
    # `outbid` incluido a propósito: al desplazado no se le devuelve la plata,
    # así que sacarlo haría bajar la barra por dinero que sí entró y se quedó.
    raised = (
        Spot.objects.filter(status__in=['confirmed', 'placed', 'outbid']).aggregate(
            total=Sum('price_paid')
        )['total']
        or 0
    )
    goal = settings.SPOT_GOAL
    percentage = min(100, int(round(raised / goal * 100))) if goal else 0
    payload = GoalSerializer({
        'goal': goal,
        'raised': raised,
        'percentage': percentage,
    }).data
    return Response(payload)