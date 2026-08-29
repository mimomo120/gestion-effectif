from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from  Collaborateur import views

urlpatterns = [
            path('rechercher/',views.filter_tableau ,name='chercher'),
            path("filter/",views.liste_par_jour,name="liste_par_jour"),
            path("filter_validation/",views.filter_validation,name="filter_validation"),
            path("operateur/",views.operateur,name="operateur"),
            path("Liste_des_operateurs/",views.operateurs,name="Liste_des_operateurs"),
            path("liste_N1_par_N2/",views.liste_N1_par_N2,name="liste_N1_par_N2"),
            path("liste_N2_par_N3/",views.liste_N2_par_N3,name="liste_N2_par_N3"),
            path("liste_N3/",views.respo_N4,name="liste_N3"),
            path("verifier",views.verifier,name="verifier")
            ]