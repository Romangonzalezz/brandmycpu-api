from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q

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
    # La foto del sticker ya pegado en el vidrio.
    #
    # `status='placed'` es una afirmación mía y nada más. Esto es la prueba, y
    # en un producto donde el comprador paga por algo físico que no puede ver,
    # es lo único que convierte la promesa en evidencia. La sube el admin.
    placed_photo = models.ImageField(upload_to='placed/', null=True, blank=True)
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
    # Clicks salientes hacia el sitio del sponsor. Denormalizado: la tabla lo
    # muestra en cada fila y contar filas de Click en cada render no escala.
    clicks_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.brand_name or '—'} ({self.size}) @ {self.position_x:.2f},{self.position_y:.2f}"

    @property
    def placed_photo_url(self):
        if self.placed_photo and hasattr(self.placed_photo, 'url'):
            return self.placed_photo.url
        return None

    @property
    def logo_url(self):
        if self.logo and hasattr(self.logo, 'url'):
            return self.logo.url
        return None

    @property
    def amount_dollars(self):
        return round(self.price_paid / 100, 2)


class Click(models.Model):
    """Un click saliente hacia el sitio de un sponsor.

    Es el número por el que un sponsor juzga si esto valió la pena, así que
    tiene que ser real: uno por dirección y nada más. No hay identificador de
    visitante en ninguna parte de esta tabla, y la IP se guarda sólo como hash
    con sal, que sirve para deduplicar y para nada más.

    El constraint hace el trabajo, no un chequeo previo. Leer y después
    insertar deja pasar dos clicks simultáneos de la misma dirección; la base
    decide y no hay carrera que perder.
    """

    spot = models.ForeignKey('Spot', on_delete=models.CASCADE, related_name='clicks')
    ip_hash = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['spot', '-created_at'])]
        constraints = [
            # Sin la condición, todos los clicks sin dirección colapsarían en
            # una sola fila y se perderían muchos más clicks reales que los
            # duplicados que evita.
            models.UniqueConstraint(
                fields=['spot', 'ip_hash'],
                condition=~Q(ip_hash=''),
                name='one_click_per_ip_per_spot',
            )
        ]

    def __str__(self):
        return f'click en {self.spot_id} el {self.created_at:%Y-%m-%d %H:%M}'


class Giveaway(models.Model):
    """Un lugar regalado, ya tomado.

    No es un canje ni un pago. Un canje lo sienta el operador y no hay
    visitante; un pago es plata. Esto es lo tercero: alguien de afuera se llevó
    un lugar que el vidrio estaba regalando, y hay que recordar que ya no está.

    `seat` es lo que lo hace seguro. Cuántos hay es configuración, y "cuántos
    quedan" es GIVEAWAY_SEATS menos las filas de acá, pero contar filas no se
    puede trabar: dos personas reclamando el último leerían el mismo número y
    pasarían las dos. Cada reclamo escribe el asiento que cree estar tomando
    bajo un constraint único, y la carrera la decide la base. Un constraint es
    una garantía y un conteo es una opinión.

    Sin campo de plata y a propósito: no se pagó nada, así que nada de acá
    puede llegar al goal. El vidrio va a decir más stickers que dólares, que es
    exactamente lo que significa un giveaway.
    """

    spot = models.OneToOneField('Spot', on_delete=models.CASCADE, related_name='giveaway')
    #: Base 1, y único: el constraint ES el control de concurrencia.
    seat = models.PositiveIntegerField(unique=True)

    #: El post que pagó el lugar. Es el recibo: se cambió vidrio por una
    #: declaración pública, y sin esto no hay forma de comprobar después que
    #: esa declaración existió.
    #: Único: un post paga UN lugar. Es lo que reemplaza al número de asiento
    #: dentro del texto, y lo hace mejor, porque no depende de que alguien
    #: copie bien una cadena.
    tweet_id = models.CharField(max_length=32, blank=True, default='')
    tweet_handle = models.CharField(max_length=32, blank=True, default='')

    #: Para avisarle que su sticker está puesto. Nunca se muestra.
    email = models.EmailField(blank=True, default='')
    #: Misma forma que Click: hash con sal, nunca una dirección.
    ip_hash = models.CharField(max_length=64, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['seat']
        constraints = [
            # Sin la condición, dos reclamos sin post (que no existen hoy, pero
            # el campo admite vacío) chocarían entre sí.
            models.UniqueConstraint(
                fields=['tweet_id'],
                condition=~Q(tweet_id=''),
                name='one_seat_per_post',
            )
        ]

    def __str__(self):
        return f'giveaway asiento {self.seat} -> {self.spot_id}'


class Visitor(models.Model):
    """Visitante anónimo detectado por heartbeat de 60s."""

    session_id = models.CharField(max_length=100, unique=True)
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Visitor {self.session_id[:8]}… (última vez {self.last_seen})"