"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from core.views import health_check
from apps.orders.whatsapp import whatsapp_webhook

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("health/", health_check, name="health_check"),
    path("api/users/", include("apps.users.urls")),
    path("api/catalog/", include("apps.catalog.urls")),
    path("api/orders/", include("apps.orders.urls")),
    path("whatsapp/webhook/", whatsapp_webhook, name="whatsapp_webhook"),
]

if settings.DEBUG:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
    from rest_framework.permissions import IsAdminUser

    class _SchemaView(SpectacularAPIView):
        permission_classes = [IsAdminUser]

    class _SwaggerView(SpectacularSwaggerView):
        permission_classes = [IsAdminUser]

    class _RedocView(SpectacularRedocView):
        permission_classes = [IsAdminUser]

    urlpatterns += [
        path("schema/", _SchemaView.as_view(), name="schema"),
        path("schema/docs/", _SwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("schema/redoc/", _RedocView.as_view(url_name="schema"), name="redoc"),
    ]
