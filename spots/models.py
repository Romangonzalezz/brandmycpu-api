from django.db import models


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
    ]

    brand_name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='logos/', null=True, blank=True)
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