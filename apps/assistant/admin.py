from django.contrib import admin

from .models import AssistantCall


@admin.register(AssistantCall)
class AssistantCallAdmin(admin.ModelAdmin):
    list_display = (
        "id", "created_at", "user", "is_admin", "outcome", "duration_ms", "tool_rounds",
        "empty_result", "feedback",
    )
    list_filter = ("outcome", "is_admin", "feedback", "empty_result", "ambiguous")
    date_hierarchy = "created_at"
    search_fields = ("user__email", "error_type")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
