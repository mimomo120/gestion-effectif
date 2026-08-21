from django.contrib import admin
from django.urls import include, path
from django.conf import settings

from  utilisateur import views

urlpatterns = [
    path('',views.login_view,name="login"),
    path('register/',views.register_view,name="register"),
    path('dashboard/',views.tableau,name='ru'),
    path("verifier/",views.verifier,name="verifier"),
    path("deconnecter/",views.deconnecter,name="deconnecter"),
    path("Dashboard/",views.dashboard_rg,name="dashboard_rg"),
    path("alerts",views.alertes,name="alerts"),
    path('changer-role/<str:nouveau_role>/', views.changer_role, name='changer_role'),
    path("test/",views.test,name="test"),
]

