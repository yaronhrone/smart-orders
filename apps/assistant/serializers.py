from rest_framework import serializers

from .models import AssistantCall


class HistoryItemSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["user", "assistant"])
    content = serializers.CharField(max_length=2000)


class AskSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=500)
    history = serializers.ListField(child=HistoryItemSerializer(), max_length=10, required=False, default=list)


class FeedbackSerializer(serializers.Serializer):
    call_id = serializers.IntegerField()
    rating = serializers.ChoiceField(choices=AssistantCall.Feedback.values)
