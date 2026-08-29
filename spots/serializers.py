from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .models import Spot

# Vidrio real, en metros. Tiene que coincidir con GLASS_* de CaseViewer.jsx.
GLASS_DEPTH_M = 0.407
GLASS_HEIGHT_M = 0.410
OVERLAP_MARGIN = 0.01

#: Un `pending` es un checkout abierto. Le reservamos el lugar un rato para que
#: dos personas no paguen por el mismo, pero no para siempre: quien abandona no
#: puede bloquear un hueco eternamente.
RESERVATION_MINUTES = 30


def _overlaps(ax, ay, aw, ah, bx, by, bw, bh):
    """Dos stickers centrados se pisan si se solapan en ambos ejes."""
    ex_a, ey_a = aw / 100 / GLASS_DEPTH_M, ah / 100 / GLASS_HEIGHT_M
    ex_b, ey_b = bw / 100 / GLASS_DEPTH_M, bh / 100 / GLASS_HEIGHT_M
    return (
        abs(ax - bx) < (ex_a + ex_b) / 2 + OVERLAP_MARGIN
        and abs(ay - by) < (ey_a + ey_b) / 2 + OVERLAP_MARGIN
    )

# Dimensiones por tamaño (cm) — tabla de precio del brief
SIZE_DIMENSIONS = {
    'small': (4.5, 4.5),
    'medium': (6.0, 5.0),
    'large': (9.5, 4.0),
}

# Precio mínimo por tamaño en centavos de dólar
SIZE_MIN_PRICE_CENTS = {
    'small': 500,
    'medium': 1000,
    'large': 2000,
}


class SpotSerializer(serializers.ModelSerializer):
    """Serializa un Spot para lectura y para creación (POST /api/spots/).

    El frontend envía `offered_price` (USD, float) que se convierte a
    `price_paid` en centavos. Si no se manda, se usa el mínimo del tamaño.
    """

    logo_url = serializers.SerializerMethodField()
    placed_photo_url = serializers.SerializerMethodField()
    offered_price = serializers.FloatField(
        write_only=True, required=False, min_value=0
    )

    class Meta:
        model = Spot
        fields = [
            'id', 'brand_name', 'website', 'x_handle', 'logo', 'logo_url',
            'placed_photo_url',
            'size', 'width_cm',
            'height_cm', 'position_x', 'position_y', 'price_paid', 'status',
            'created_at', 'offered_price', 'datafast_visitor_id',
        ]
        read_only_fields = [
            'id', 'logo_url', 'placed_photo_url', 'price_paid', 'status',
            'created_at',
        ]
        extra_kwargs = {
            # El comprador adjunta el logo tal cual: se guarda sin reprocesar.
            'logo': {'required': False, 'write_only': True},
            # El sitio se pide siempre: es lo que muestra la tarjeta del
            # sponsor cuando alguien apunta su sticker. El de X es opcional.
            'website': {'required': True, 'allow_blank': False},
            'x_handle': {'required': False, 'allow_blank': True},
            'datafast_visitor_id': {
                'required': False, 'allow_blank': True, 'write_only': True,
            },
            'width_cm': {'required': False},
            'height_cm': {'required': False},
            'position_x': {'required': False},
            'position_y': {'required': False},
        }

    def get_placed_photo_url(self, obj):
        url = obj.placed_photo_url
        if not url:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(url) if request else url

    def get_logo_url(self, obj):
        """URL absoluta: el frontend vive en otro dominio y una ruta relativa
        se resolvería contra él, no contra la API."""
        url = obj.logo_url
        if not url:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(url) if request else url

    def validate(self, attrs):
        size = attrs.get('size')
        if size and size not in SIZE_DIMENSIONS:
            raise serializers.ValidationError({'size': 'That size is not valid.'})

        if size:
            # Asignadas, no `setdefault`: el precio sale del tamaño, así que si
            # las medidas las eligiera el comprador, un 'small' de 40x40 cm se
            # lleva medio vidrio por $5.
            attrs['width_cm'], attrs['height_cm'] = SIZE_DIMENSIONS[size]

        # Posición normalizada sobre el vidrio (por defecto centrado).
        attrs.setdefault('position_x', 0.5)
        attrs.setdefault('position_y', 0.5)

        # El sticker entero tiene que entrar, no sólo su centro.
        #
        # `0 <= centro <= 1` alcanzaba mientras el comprador sólo podía elegir
        # huecos de la grilla, que nunca caen cerca del filo. Arrastrando libre
        # sí llega: un small centrado en 0.98 cuelga la mitad fuera del vidrio
        # y esta validación lo daba por bueno.
        half_x = attrs['width_cm'] / 100 / GLASS_DEPTH_M / 2
        half_y = attrs['height_cm'] / 100 / GLASS_HEIGHT_M / 2
        for axis, half in (('position_x', half_x), ('position_y', half_y)):
            if not half <= attrs[axis] <= 1 - half:
                raise serializers.ValidationError({
                    axis: 'The whole sticker has to fit on the glass.'
                })

        offered = attrs.pop('offered_price', None)
        if size:
            min_cents = SIZE_MIN_PRICE_CENTS[size]
            if offered is None:
                attrs['price_paid'] = min_cents
            else:
                cents = int(round(offered * 100))
                if cents < min_cents:
                    raise serializers.ValidationError({
                        'offered_price': (
                            f"The minimum for a '{size}' spot is "
                            f"${min_cents / 100:.0f}."
                        )
                    })
                attrs['price_paid'] = cents
        else:
            attrs['price_paid'] = int(round((offered or 5) * 100))

        if attrs['price_paid'] <= 0:
            raise serializers.ValidationError({'price_paid': 'Invalid price.'})

        # El frontend ya esconde los huecos ocupados, pero es el navegador del
        # comprador: acá se decide. Sin esto dos sponsors pueden pagar por el
        # mismo centímetro de vidrio y no hay forma de cumplirle a los dos.
        def hits(other):
            return _overlaps(
                attrs['position_x'], attrs['position_y'],
                attrs['width_cm'], attrs['height_cm'],
                other.position_x, other.position_y,
                other.width_cm, other.height_cm,
            )

        # Un checkout abierto NO se puede superar. La plata todavía no entró,
        # así que no hay oferta que ganarle: el lugar está reservado hasta que
        # expire la ventana. Sin esto, dos personas pagan por el mismo hueco y
        # la segunda desplaza a alguien que ni llegó a estar en el vidrio.
        cutoff = timezone.now() - timedelta(minutes=RESERVATION_MINUTES)
        for other in Spot.objects.filter(status='pending', created_at__gte=cutoff):
            if hits(other):
                raise serializers.ValidationError({
                    'position_x': 'Someone is checking out that spot right now. '
                                  'Try again in a few minutes.'
                })

        # Un sticker ya pagado sí se puede superar: se lo lleva quien ponga más
        # que TODO lo que tapa.
        #
        # La suma y no "más que cada uno": un large de $21 tapa hasta tres
        # smalls, y superándolos de a uno se llevaría $60 de vidrio por $21.
        standing = [
            o for o in Spot.objects.filter(status__in=['confirmed', 'placed'])
            if hits(o)
        ]
        if standing:
            floor = sum(o.price_paid for o in standing)
            if attrs['price_paid'] <= floor:
                raise serializers.ValidationError({
                    'offered_price': (
                        f'That spot is taken. Bid more than '
                        f'${floor / 100:.0f} to take it.'
                    )
                })

        return attrs


class ActivitySerializer(serializers.ModelSerializer):
    """Payload liviano para el feed de actividad (últimos confirmados)."""

    class Meta:
        model = Spot
        fields = [
            'id', 'brand_name', 'website', 'x_handle', 'size', 'price_paid',
            'created_at',
        ]


class GoalSerializer(serializers.Serializer):
    goal = serializers.IntegerField()
    raised = serializers.IntegerField()
    percentage = serializers.IntegerField()