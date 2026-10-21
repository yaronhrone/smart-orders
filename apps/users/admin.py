from django.contrib import admin, messages

from .models import User, Profile, ApiToken

admin.site.register(User)
admin.site.register(Profile)


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "name", "created_at", "last_used_at", "revoked")
    list_filter = ("revoked",)
    search_fields = ("user__email", "name")
    fields = ("user", "name", "revoked")
    readonly_fields = ("created_at", "last_used_at")

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        # The raw token exists only now; store the hash and show it once.
        token, raw = ApiToken.issue(obj.user, obj.name)
        obj.pk = token.pk
        obj.token_hash = token.token_hash
        messages.warning(request, f"הטוקן (מוצג פעם אחת בלבד, שמור אותו): {raw}")
