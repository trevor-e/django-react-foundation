"""A small URLconf for exercising OpenAPI generation against real routes."""

from django.http import HttpResponse
from django.urls import path


def widget_list(request):
    return HttpResponse()


def widget_detail(request, widget_id):
    return HttpResponse()


def internal_thing(request):
    return HttpResponse()


urlpatterns = [
    path("api/widgets", widget_list, name="widget-list"),
    path("api/widgets/<int:widget_id>", widget_detail, name="widget-detail"),
    path("api/internal", internal_thing, name="internal-thing"),
    path("not-api/thing", internal_thing, name="non-api-thing"),
]
