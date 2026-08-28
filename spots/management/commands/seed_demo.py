from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from spots.models import Spot, Visitor
from spots.serializers import SIZE_DIMENSIONS


class Command(BaseCommand):
    """Carga datos de ejemplo para ver la landing viva en local.

    Uso: python manage.py seed_demo
    """

    help = 'Crea spots y visitantes de ejemplo.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Borra todo antes de cargar.')

    def handle(self, *args, **options):
        if options['reset']:
            Spot.objects.all().delete()
            Visitor.objects.all().delete()
            self.stdout.write('Datos previos borrados.')

        spots_spec = [
            {'brand': 'Vercel',  'size': 'large',  'status': 'placed',    'price': 2000, 'x': 0.30, 'y': 0.62},
            {'brand': 'Turso',   'size': 'small',  'status': 'confirmed', 'price': 500,  'x': 0.68, 'y': 0.30},
            {'brand': 'Railway', 'size': 'medium', 'status': 'confirmed', 'price': 1200, 'x': 0.45, 'y': 0.18},
            {'brand': '',        'size': 'small',  'status': 'pending',   'price': 500,  'x': 0.55, 'y': 0.75},
            {'brand': '',        'size': 'small',  'status': 'pending',   'price': 500,  'x': 0.22, 'y': 0.40},
        ]
        for spec in spots_spec:
            w, h = SIZE_DIMENSIONS[spec['size']]
            Spot.objects.create(
                brand_name=spec['brand'],
                size=spec['size'],
                width_cm=w,
                height_cm=h,
                position_x=spec['x'],
                position_y=spec['y'],
                price_paid=spec['price'],
                status=spec['status'],
                created_at=timezone.now() - timedelta(hours=len(spots_spec) * 2, seconds=(len(spots_spec) - spots_spec.index(spec)) * 3600),
            )
        self.stdout.write(self.style.SUCCESS(f'{len(spots_spec)} spots creados.'))

        for i in range(14):
            Visitor.objects.create(
                session_id=f'demo-{i}-{timezone.now().timestamp()}',
                last_seen=timezone.now() - timedelta(minutes=i * 3),
            )
        self.stdout.write(self.style.SUCCESS('14 visitantes creados (algunos live).'))