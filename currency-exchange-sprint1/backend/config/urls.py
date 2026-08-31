from django.contrib import admin
from django.urls import include, path
from core import views

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("account/", views.account, name="account"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    path("delete-account/", views.delete_account_view, name="delete_account"),
    path("oidc/", include("mozilla_django_oidc.urls")),
]
