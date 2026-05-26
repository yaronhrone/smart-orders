from rest_framework import generics, permissions
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .serializers import RegisterSerializer, UserSerializer, AdminUserSerializer , UserWithProfileSerializer
from django.contrib.auth import get_user_model
from rest_framework.response import Response
User = get_user_model()


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()

        return Response(UserWithProfileSerializer(user).data)


class MeView(generics.RetrieveAPIView):
    serializer_class = UserWithProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class AdminUserListView(generics.ListAPIView):
    queryset = User.objects.select_related("profile").all().order_by("-date_joined")
    serializer_class = UserWithProfileSerializer
    permission_classes = [permissions.IsAdminUser]


class AdminUserDetailView(generics.RetrieveDestroyAPIView):
    queryset = User.objects.select_related("profile").all()
    serializer_class = UserWithProfileSerializer
    permission_classes = [permissions.IsAdminUser]
