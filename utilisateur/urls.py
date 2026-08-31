from django.contrib import admin
from django.urls import include, path
from django.conf import settings

from  utilisateur import views

urlpatterns = [
    path('',views.login_view,name="login"),
    path('register/',views.register_view,name="register"),
    path('dashboard/',views.tableau,name='dashboard_N1'),
    path("verifier/",views.verifier,name="verifier"),
    path("deconnecter/",views.deconnecter,name="deconnecter"),
    path("Dashboard/N2/",views.dashboard_N2,name="dashboard_N2"),
    path('changer-role/<str:nouveau_role>/', views.changer_role, name='changer_role'),
    path("votre-url-alertes/",views.alerts,name="votre-url-alertes"),
    path("Dashboard/Admin/",views.SUPER_dashboard,name="SUPER"),
    path("utilisateurs/ajouter/",views.ajouter_user,name="ajouter_user"),
    path("utilisateurs/supprimer/<str:id>/", views.supprimer_user, name="supprimer_user"),
    path("utilisateurs/modifier/<str:id>/", views.modifier_user, name="modifier_user"),
    path("Dashboard/", views.dashboard_rh, name="dashboard"),
    path('utilisateurs/changer-mot-de-passe/', views.changer_mot_de_passe, name='changer_mot_de_passe'),
]

