from django.contrib import admin
from django.utils.html import format_html

from .models import Spot, Visitor


@admin.register(Spot)
class SpotAdmin(admin.ModelAdmin):
    list_display = [
        'brand_name', 'size', 'width_cm', 'height_cm', 'price_paid',
        'status', 'proof', 'position_x', 'position_y', 'created_at',
    ]
    list_filter = ('status', 'size')
    search_fields = ('brand_name', 'payment_id')
    readonly_fields = ('created_at', 'logo_preview', 'placed_photo_preview')
    actions = ('mark_placed',)

    @admin.display(description='Proof', boolean=True)
    def proof(self, obj):
        """Se ve de un vistazo a quién le falta la foto del sticker pegado."""
        return bool(obj.placed_photo)

    @admin.action(description='Marcar como pegado en el vidrio')
    def mark_placed(self, request, queryset):
        sin_foto = queryset.filter(placed_photo='').count() + queryset.filter(
            placed_photo__isnull=True
        ).count()
        updated = queryset.update(status='placed')
        msg = f'{updated} marcados como pegados.'
        if sin_foto:
            # `placed` sin foto es exactamente la afirmación sin prueba que la
            # foto vino a reemplazar.
            msg += f' {sin_foto} sin foto: subila antes de que alguien pregunte.'
        self.message_user(request, msg)

    def placed_photo_preview(self, obj):
        if obj.placed_photo:
            return format_html(
                '<img src="{}" style="max-height:220px;border-radius:6px;'
                'border:1px solid #222"/>',
                obj.placed_photo.url,
            )
        return '—'

    placed_photo_preview.short_description = 'Sticker on the glass'

    def logo_preview(self, obj):
        if obj.logo:
            return format_html(
                '<img src="{}" style="max-height:80px;border-radius:4px;'
                'border:1px solid #222"/>',
                obj.logo.url,
            )
        return '—'

    logo_preview.short_description = 'Logo'


@admin.register(Visitor)
class VisitorAdmin(admin.ModelAdmin):
    list_display = ('session_id', 'last_seen', 'created_at')
    search_fields = ('session_id',)
    readonly_fields = ('session_id', 'created_at')