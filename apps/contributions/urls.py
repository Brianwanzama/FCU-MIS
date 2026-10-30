from django.urls import path

from .views import (
    ContributionCreateView,
    ContributionDetailView,
    ContributionListView,
    ContributionDeleteView,
    ContributionUpdateView,
    ReversalDecisionView,
    ReversalListView,
    ReversalRequestView,
)

app_name = "contributions"

urlpatterns = [
    path("", ContributionListView.as_view(), name="list"),
    path("new/", ContributionCreateView.as_view(), name="create"),
    path("reversals/", ReversalListView.as_view(), name="reversals"),
    path(
        "reversals/<int:pk>/<str:decision>/",
        ReversalDecisionView.as_view(),
        name="reversal_decision",
    ),
    path("<int:pk>/", ContributionDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", ContributionUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", ContributionDeleteView.as_view(), name="delete"),
    path("<int:pk>/reverse/", ReversalRequestView.as_view(), name="reverse"),
]
