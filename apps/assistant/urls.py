from django.urls import path

from .views import AskView, FeedbackView, StatsView

urlpatterns = [
    path("ask/", AskView.as_view(), name="assistant-ask"),
    path("feedback/", FeedbackView.as_view(), name="assistant-feedback"),
    path("stats/", StatsView.as_view(), name="assistant-stats"),
]
