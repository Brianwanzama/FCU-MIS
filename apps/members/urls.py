from django.urls import path

from .views import MemberDetailView, MemberListView

app_name = "members"

urlpatterns = [
    path("", MemberListView.as_view(), name="list"),
    path("<slug:member_code>/", MemberDetailView.as_view(), name="detail"),
]