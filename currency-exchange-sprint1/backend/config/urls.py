from django.contrib import admin
from django.urls import include, path
from core import views

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("account/", views.account, name="account"),
    path("trade/", views.trade, name="trade"),
    path("currencies/", views.currency_management, name="currency_management"),
    path("manage/users/", views.admin_users, name="admin_users"),
    path("manage/users/new/", views.admin_user_create, name="admin_user_create"),
    path("manage/users/<str:user_id>/roles/", views.admin_user_roles, name="admin_user_roles"),
    path("manage/users/<str:user_id>/delete/", views.admin_user_delete, name="admin_user_delete"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    path("delete-account/", views.delete_account_view, name="delete_account"),
    path("oidc/", include("mozilla_django_oidc.urls")),
]
