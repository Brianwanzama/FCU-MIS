from django.urls import path

from .views import (
    ContributionCreateView,
)

app_name = "contributions"

urlpatterns = [
    path(
        "",
        ContributionCreateView.as_view(),
        name="create",
    ),
]