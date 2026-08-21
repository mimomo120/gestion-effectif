from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from  Collaborateur import views

urlpatterns = [
            path('rechercher/',views.filter_tableau ,name='chercher'),
            path("filter/",views.liste_par_jour,name="liste_par_jour"),path("filter_validation/",views.filter_validation,name="filter_validation"),
            path("operateur/",views.operateur,name="operateur"),
            path("Liste_des_operateurs/",views.operateurs,name="Liste_des_operateurs"),
            path("liste_Ru_par_Rg/",views.liste_Ru_par_Rg,name="RG"),
            path("liste_Rg_par_DUR/",views.liste_Rg_par_dur,name="liste_Rg_par_dur"),
            path("liste_N3/",views.respo_N4,name="liste_N3"),
            ]