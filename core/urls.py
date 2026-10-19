from django.urls import path

from core.usage_views import AIUsageView

urlpatterns = [path("ai-usage/", AIUsageView.as_view(), name="ai-usage")]
