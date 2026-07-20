from django.urls import path

from .views import (
    ContributionCreateView,
    ContributionListView,
)

app_name = "contributions"

urlpatterns = [
    path(
        "",
        ContributionListView.as_view(),
        name="list",
    ),
    path(
        "new/",
        ContributionCreateView.as_view(),
        name="create",
    ),
]