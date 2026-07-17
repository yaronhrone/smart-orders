from rest_framework import generics, permissions
from rest_framework_simplejwt.views import TokenObtainPairView
from django.shortcuts import get_object_or_404
from .serializers import RegisterSerializer, UserSerializer, AdminUserSerializer, UserWithProfileSerializer, ProfileSerializer, HebrewTokenObtainPairSerializer
from django.contrib.auth import get_user_model
from rest_framework.response import Response

User = get_user_model()


class HebrewLoginView(TokenObtainPairView):
    serializer_class = HebrewTokenObtainPairSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.IsAdminUser]

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


class AdminUserProfileUpdateView(generics.UpdateAPIView):
    """PATCH /api/users/admin/users/<id>/profile/ — admin edits any user's profile."""
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_object(self):
        user = get_object_or_404(User, pk=self.kwargs["pk"])
        return user.profile

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


class ProfileUpdateView(generics.UpdateAPIView):
    """PATCH /api/users/me/profile/ — update the current user's company/personal profile."""
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user.profile

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)
