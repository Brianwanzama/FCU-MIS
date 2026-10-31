from django.urls import path

from . import views

app_name = "unittrust"

urlpatterns = [
    path("", views.PortfolioView.as_view(), name="portfolio"),
    path("new/", views.InvestmentCreateView.as_view(), name="create"),
    path("other-savings/", views.OtherSavingsView.as_view(), name="other_savings"),
    path("other-savings/<str:action>/", views.OtherSavingsActionView.as_view(), name="other_savings_action"),
    path("other-savings/<str:action>/<int:pk>/", views.OtherSavingsActionView.as_view(), name="other_savings_action_pk"),
    path("profit/", views.ProfitView.as_view(), name="profit"),
    path("profit/<int:pk>/<str:action>/", views.ProfitActionView.as_view(), name="profit_action"),
    path("distributions/", views.DistributionListView.as_view(), name="distributions"),
    path("distributions/<int:pk>/", views.DistributionDetailView.as_view(), name="distribution"),
    path("distributions/<int:pk>/<str:action>/", views.DistributionActionView.as_view(), name="distribution_action"),
    path("transactions/<int:pk>/reverse/", views.TransactionReverseView.as_view(), name="transaction_reverse"),
    path("transactions/<int:pk>/delete/", views.TransactionDeleteView.as_view(), name="transaction_delete"),
    path("distributions/<int:pk>/delete/", views.DistributionDeleteView.as_view(), name="distribution_delete"),
    path("<int:pk>/delete/", views.InvestmentDeleteView.as_view(), name="delete"),
    path("<int:pk>/", views.InvestmentDetailView.as_view(), name="detail"),
    path("<int:pk>/transaction/", views.TransactionCreateView.as_view(), name="transaction"),
    path("<int:pk>/close/", views.InvestmentCloseView.as_view(), name="close"),
]
