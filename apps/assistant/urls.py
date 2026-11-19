from django.urls import path

from .views import AskView, FeedbackView, SpendingView, StatsView

urlpatterns = [
    path("ask/", AskView.as_view(), name="assistant-ask"),
    path("spending/", SpendingView.as_view(), name="assistant-spending"),
    path("feedback/", FeedbackView.as_view(), name="assistant-feedback"),
    path("stats/", StatsView.as_view(), name="assistant-stats"),
]
