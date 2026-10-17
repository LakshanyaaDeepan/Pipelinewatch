from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("projects/<slug:slug>/", views.project_detail, name="project_detail"),
    path("builds/<int:pk>/", views.build_detail, name="build_detail"),
    path("api/status/", views.api_status, name="api_status"),
    path("webhooks/<slug:slug>/", views.webhook, name="webhook"),
]
