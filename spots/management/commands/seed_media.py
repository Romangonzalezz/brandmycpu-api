import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Copia el media versionado del repo al MEDIA_ROOT real.

    Un volumen de Railway se monta vacío y tapa el directorio que venía en la
    imagen, así que sin esto el logo del canje desaparece justo el día que se
    monta el volumen que existe para que no desaparezca.

    Sólo escribe lo que falta. Nunca pisa un archivo que ya está: en el volumen
    viven los logos que subieron los sponsors, y el repo no sabe nada de ellos.

    Uso: python manage.py seed_media
    """

    help = 'Copia backend/media/ al MEDIA_ROOT si allí falta algún archivo.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué copiaría, sin copiar.',
        )

    def handle(self, *args, **options):
        source = Path(settings.BASE_DIR) / 'media'
        target = Path(settings.MEDIA_ROOT)

        if not source.is_dir():
            self.stdout.write('No hay media/ versionado. Nada que copiar.')
            return

        if source.resolve() == target.resolve():
            self.stdout.write(
                'MEDIA_ROOT es el media/ del repo: no hay volumen montado, '
                'no hay nada que copiar.'
            )
            return

        copied = skipped = 0
        for src in source.rglob('*'):
            if not src.is_file():
                continue
            dst = target / src.relative_to(source)
            if dst.exists():
                skipped += 1
                continue
            if options['dry_run']:
                self.stdout.write(f'copiaría {dst}')
                copied += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            self.stdout.write(f'copiado {dst}')
            copied += 1

        self.stdout.write(
            self.style.SUCCESS(f'{copied} copiados, {skipped} ya estaban en {target}.')
        )
