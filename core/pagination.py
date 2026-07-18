from rest_framework import serializers
from rest_framework.pagination import BasePagination
from rest_framework.response import Response


def paginated_response_serializer(item_serializer_class):
    """Builds a `{results: [...], has_more: bool}` serializer for OpenAPI schemas."""
    return type(
        f"Paginated{item_serializer_class.__name__}",
        (serializers.Serializer,),
        {
            "results": item_serializer_class(many=True),
            "has_more": serializers.BooleanField(),
        },
    )


class LoadMorePagination(BasePagination):
    """
    "Load more" pagination: reads `limit`/`offset` query params (falling back
    to `default_limit`/0) and fetches one extra row past `limit` to determine
    `has_more` without a separate COUNT query.

    `?all=1`/`?all=true` bypasses pagination entirely (for callers that need
    the full collection, e.g. client-side search over the whole catalog).
    """
    default_limit = 20
    has_more = False

    def paginate_queryset(self, queryset, request, view=None):
        if request.query_params.get("all") in ("1", "true"):
            self.has_more = False
            return list(queryset)

        try:
            limit = int(request.query_params.get("limit", self.default_limit))
        except (TypeError, ValueError):
            limit = self.default_limit
        try:
            offset = int(request.query_params.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0

        limit = max(1, min(limit, self.default_limit * 5))
        offset = max(0, offset)

        items = list(queryset[offset: offset + limit + 1])
        self.has_more = len(items) > limit
        return items[:limit]

    def get_paginated_response(self, data):
        return Response({"results": data, "has_more": self.has_more})

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "results": {"type": "array", "items": schema},
                "has_more": {"type": "boolean"},
            },
            "required": ["results", "has_more"],
        }


class LoadMorePagination10(LoadMorePagination):
    default_limit = 10


def paginate(request, queryset, default_limit):
    """
    Manual variant of `LoadMorePagination` for plain APIViews that build
    their response by hand (not through a serializer/queryset flow DRF's
    generic views can drive). Returns (page_items, has_more).
    """
    paginator = LoadMorePagination()
    paginator.default_limit = default_limit
    page = paginator.paginate_queryset(queryset, request)
    return page, paginator.has_more
