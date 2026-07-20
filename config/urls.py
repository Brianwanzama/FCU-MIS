from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),

    # Authentication & Users
    path("accounts/", include("apps.accounts.urls")),

    # Core Modules
    path("documents/", include("apps.documents.urls")),
    path("cycles/", include("apps.cycles.urls")),
    path("contributions/", include("apps.contributions.urls")),

    # Dashboard
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)