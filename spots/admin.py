from django.contrib import admin
from django.utils.html import format_html

from .models import Spot, Visitor


@admin.register(Spot)
class SpotAdmin(admin.ModelAdmin):
    list_display = [
        'brand_name', 'size', 'width_cm', 'height_cm', 'price_paid',
        'status', 'position_x', 'position_y', 'created_at',
    ]
    list_filter = ('status', 'size')
    search_fields = ('brand_name', 'payment_id')
    readonly_fields = ('created_at', 'logo_preview')

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