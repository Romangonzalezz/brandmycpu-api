from rest_framework import serializers

from .models import Spot

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
    offered_price = serializers.FloatField(
        write_only=True, required=False, min_value=0
    )

    class Meta:
        model = Spot
        fields = [
            'id', 'brand_name', 'logo_url', 'size', 'width_cm', 'height_cm',
            'position_x', 'position_y', 'price_paid', 'status', 'created_at',
            'offered_price',
        ]
        read_only_fields = [
            'id', 'logo_url', 'price_paid', 'status', 'created_at',
        ]
        extra_kwargs = {
            'width_cm': {'required': False},
            'height_cm': {'required': False},
            'position_x': {'required': False},
            'position_y': {'required': False},
        }

    def get_logo_url(self, obj):
        return obj.logo_url

    def validate(self, attrs):
        size = attrs.get('size')
        if size and size not in SIZE_DIMENSIONS:
            raise serializers.ValidationError({'size': 'Tamaño no válido.'})

        if size:
            w, h = SIZE_DIMENSIONS[size]
            attrs.setdefault('width_cm', w)
            attrs.setdefault('height_cm', h)

        # Posición normalizada 0-1 (por defecto centrado)
        attrs.setdefault('position_x', 0.5)
        attrs.setdefault('position_y', 0.5)

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
                            f"El mínimo para un spot '{size}' es "
                            f"${min_cents / 100:.0f}."
                        )
                    })
                attrs['price_paid'] = cents
        else:
            attrs['price_paid'] = int(round((offered or 5) * 100))

        if attrs['price_paid'] <= 0:
            raise serializers.ValidationError({'price_paid': 'Precio inválido.'})
        return attrs


class ActivitySerializer(serializers.ModelSerializer):
    """Payload liviano para el feed de actividad (últimos confirmados)."""

    class Meta:
        model = Spot
        fields = ['id', 'brand_name', 'size', 'price_paid', 'created_at']


class GoalSerializer(serializers.Serializer):
    goal = serializers.IntegerField()
    raised = serializers.IntegerField()
    percentage = serializers.IntegerField()