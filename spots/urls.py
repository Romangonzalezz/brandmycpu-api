from django.urls import path

from . import views

urlpatterns = [
    # Visitors
    path('visitors/heartbeat/', views.visitor_heartbeat, name='visitor-heartbeat'),
    path('visitors/count/', views.visitor_count, name='visitor-count'),

    # Spots
    path('spots/', views.spots_endpoint, name='spots-list'),
    path('spots/activity/', views.spot_activity, name='spots-activity'),
    path('spots/webhook/', views.spot_webhook, name='spots-webhook'),

    # Goal
    path('goal/', views.goal, name='goal'),
]