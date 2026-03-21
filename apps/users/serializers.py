from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from apps.users.models import Profile
from apps.catalog.models import Region

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True)

    company_name = serializers.CharField(write_only=True)
    company_phone = serializers.CharField(allow_blank=True ,write_only=True)
    company_address = serializers.CharField(write_only=True , allow_blank=True)
    phone = serializers.CharField(write_only=True, allow_blank=True)
    position = serializers.CharField(required=False, allow_blank=True)
    region = serializers.ChoiceField(choices=Region.choices , write_only=True)


    class Meta:
        model = User
        fields = ("email", "password", "password2", "first_name", "last_name"
                  , "company_name", "company_phone", "company_address", "phone", "position", "region")

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")
        password = validated_data.pop("password")

        user_fields = {
            "email": validated_data.pop("email"),
            "first_name": validated_data.pop("first_name", ""),
            "last_name": validated_data.pop("last_name", ""),
        }
        user = User(**user_fields)
        user.set_password(password)
        user.save()

        Profile.objects.create(
            user=user,
            **validated_data
        )
        return user
class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = (
            "company_name",
            "company_phone",
            "company_address",
            "phone",
            "position",
            "region",
        )
class UserWithProfileSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer()

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "profile")
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name")


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "is_active", "is_staff", "date_joined")
