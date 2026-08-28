import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import services
from .models import Spot, Visitor
from .serializers import ActivitySerializer, GoalSerializer, SpotSerializer

logger = logging.getLogger('brandmycpu.api')

# Ventana de "visitante live": últimos 30 minutos
LIVE_WINDOW_MINUTES = 30


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
        return Response(SpotSerializer(queryset, many=True).data)

    # POST — crea el spot pending y un checkout de DodoPayments
    serializer = SpotSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    spot = serializer.save()

    try:
        checkout = services.create_checkout(
            amount_cents=spot.price_paid,
            return_url=settings.DODO_RETURN_URL,
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
            spot.status = 'confirmed'
            updated = True
        spot.save(update_fields=['status', 'payment_id'])

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
    raised = (
        Spot.objects.filter(status__in=['confirmed', 'placed']).aggregate(
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