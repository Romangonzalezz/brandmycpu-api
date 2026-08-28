from django.core.validators import FileExtensionValidator
from django.db import models

# Formatos que aceptamos tal cual del comprador (no se reprocesan).
LOGO_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp', 'svg']


class Spot(models.Model):
    """Un sticker (spot) comprado para el vidrio lateral del gabinete."""

    SIZE_CHOICES = [
        ('small', 'Small'),
        ('medium', 'Medium'),
        ('large', 'Large'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('placed', 'Placed'),
        # Le ganaron el lugar pagando más. No se borra ni se reembolsa: la
        # fila queda para el historial y su plata sigue contando en el goal.
        ('outbid', 'Outbid'),
    ]

    brand_name = models.CharField(max_length=100)
    # Sitio del sponsor: se muestra al pasar el mouse sobre su sticker.
    website = models.URLField(max_length=300, blank=True)
    # Usuario de X, sin arroba. Opcional.
    x_handle = models.CharField(max_length=15, blank=True)
    # Cookie de DataFast: sin ella el pago aparece en su globo pero sin
    # canal de origen.
    datafast_visitor_id = models.CharField(max_length=100, blank=True)
    # FileField y no ImageField: el SVG es un formato aceptado y Pillow no
    # lo valida. Nota de seguridad: un SVG servido desde el mismo dominio
    # puede ejecutar scripts, así que MEDIA debería salir por otro host o
    # con Content-Disposition: attachment.
    logo = models.FileField(
        upload_to='logos/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(LOGO_EXTENSIONS)],
    )
    size = models.CharField(max_length=10, choices=SIZE_CHOICES)
    width_cm = models.FloatField()
    height_cm = models.FloatField()
    # Posición normalizada 0-1 sobre el vidrio del gabinete
    position_x = models.FloatField(default=0.5)
    position_y = models.FloatField(default=0.5)
    # Precio en centavos de dólar (modelo del brief: IntegerField USD cents)
    price_paid = models.IntegerField(default=0)
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default='pending'
    )
    # ID del pago devuelto por DodoPayments (para idempotencia del webhook)
    payment_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.brand_name or '—'} ({self.size}) @ {self.position_x:.2f},{self.position_y:.2f}"

    @property
    def logo_url(self):
        if self.logo and hasattr(self.logo, 'url'):
            return self.logo.url
        return None

    @property
    def amount_dollars(self):
        return round(self.price_paid / 100, 2)


class Visitor(models.Model):
    """Visitante anónimo detectado por heartbeat de 60s."""

    session_id = models.CharField(max_length=100, unique=True)
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Visitor {self.session_id[:8]}… (última vez {self.last_seen})"