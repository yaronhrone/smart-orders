from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

import logging
logger = logging.getLogger(__name__)

def custom_exception_handler(exc, context):
    """Custom exception handler that logs errors and returns a consistent error response."""
    response = exception_handler(exc, context)

    if response is not None:
        return Response({
            "error": True,
             "message": response.data,
             "status": response.status_code
             },
            status=response.status_code
        )

    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    return Response(
        {"error": True,
          "message": "Internal Server Error." ,
          "status": status.HTTP_500_INTERNAL_SERVER_ERROR
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )