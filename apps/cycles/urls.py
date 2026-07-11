from django.urls import path

from . import views

app_name = "cycles"

urlpatterns = [
    path("", views.cycle_list, name="list"),
    path("new/", views.cycle_create, name="create"),
    path("<int:pk>/", views.cycle_detail, name="detail"),
    path("<int:pk>/edit/", views.cycle_edit, name="edit"),
    path("<int:pk>/activate/", views.cycle_activate, name="activate"),
    path("<int:pk>/close/", views.cycle_close, name="close"),
    path("<int:pk>/reopen/", views.cycle_reopen, name="reopen"),
    path("<int:pk>/delete/", views.cycle_delete, name="delete"),
]
