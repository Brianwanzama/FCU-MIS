from django.urls import path

from . import views

app_name = "unittrust"

urlpatterns = [
    path("", views.PortfolioView.as_view(), name="portfolio"),
    path("new/", views.InvestmentCreateView.as_view(), name="create"),
    path("distributions/", views.DistributionListView.as_view(), name="distributions"),
    path("distributions/<int:pk>/", views.DistributionDetailView.as_view(), name="distribution"),
    path("distributions/<int:pk>/<str:action>/", views.DistributionActionView.as_view(), name="distribution_action"),
    path("transactions/<int:pk>/reverse/", views.TransactionReverseView.as_view(), name="transaction_reverse"),
    path("<int:pk>/", views.InvestmentDetailView.as_view(), name="detail"),
    path("<int:pk>/transaction/", views.TransactionCreateView.as_view(), name="transaction"),
    path("<int:pk>/close/", views.InvestmentCloseView.as_view(), name="close"),
]
