from django.urls import path

from .views import AuthorizeView, RegisterView, TokenView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="oauth-register"),
    path("authorize/", AuthorizeView.as_view(), name="oauth-authorize"),
    path("token/", TokenView.as_view(), name="oauth-token"),
]
